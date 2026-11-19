"""
Pseudobulk differential expression: CD14 monocytes, IFN-beta vs PBS.

Steps
1. Select CD14 Mono cells treated with IFN-beta or PBS.
2. Drop donors with < 5 cells in either condition.
3. Sum raw counts per donor x condition (pseudobulk).
4. Keep genes with >= 10 total counts across all pseudobulk samples.
5. pyDESeq2, paired design ~ donor + condition, contrast IFN-beta vs PBS.
6. Count DEGs with padj < 0.05 and |log2FC| > 1.
"""
import json
import os
import sys
import time

import h5py
import numpy as np
import pandas as pd
import scipy.sparse as sp
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats

WS = "/workspace"
H5 = os.path.join(WS, "data", "Parse_10M_PBMC_100K_subset.h5ad")
CELL_TYPE = "CD14 Mono"
TEST, REF = "IFN-beta", "PBS"
MIN_CELLS = 5
MIN_GENE_COUNTS = 10
PADJ_CUT, LFC_CUT = 0.05, 1.0

t0 = time.time()
f = h5py.File(H5, "r")


def read_cat(key):
    g = f["obs"][key]
    cats = np.array([c.decode() if isinstance(c, bytes) else c for c in g["categories"][:]], dtype=object)
    codes = g["codes"][:]
    out = cats[codes]
    out[codes < 0] = None
    return out


obs = pd.DataFrame({k: read_cat(k) for k in ["cell_type", "cytokine", "donor", "sample"]})
obs.index = np.arange(len(obs))
var_names = np.array([c.decode() if isinstance(c, bytes) else c for c in f["var"]["_index"][:]], dtype=object)
n_genes = len(var_names)

# 1. cell selection
sel = obs[(obs.cell_type == CELL_TYPE) & (obs.cytokine.isin([TEST, REF]))].copy()
print(f"CD14 Mono cells in {TEST}/{REF}: {len(sel)}")

# 2. donor filter
ct = pd.crosstab(sel.donor, sel.cytokine)
print("cells per donor x condition:\n", ct)
keep_donors = ct.index[(ct[TEST] >= MIN_CELLS) & (ct[REF] >= MIN_CELLS)].tolist()
drop_donors = sorted(set(ct.index) - set(keep_donors))
print(f"kept donors ({len(keep_donors)}): {keep_donors}")
print(f"dropped donors ({len(drop_donors)}): {drop_donors}")
sel = sel[sel.donor.isin(keep_donors)].copy()
print(f"cells after donor filter: {len(sel)}")

# 3. read only the selected rows of the CSR matrix (raw counts) from HDF5
rows = np.sort(sel.index.values)
indptr = f["X"]["indptr"][:]
Xd, Xi = f["X"]["data"], f["X"]["indices"]
# merge consecutive rows into contiguous ranges to cut the number of HDF5 reads
starts, ends = indptr[rows], indptr[rows + 1]
data_parts, idx_parts = [], []
i = 0
while i < len(rows):
    j = i
    while j + 1 < len(rows) and rows[j + 1] == rows[j] + 1:
        j += 1
    a, b = starts[i], ends[j]
    data_parts.append(Xd[a:b])
    idx_parts.append(Xi[a:b])
    i = j + 1
data = np.concatenate(data_parts)
indices = np.concatenate(idx_parts)
new_indptr = np.concatenate([[0], np.cumsum(ends - starts)])
X = sp.csr_matrix((data, indices, new_indptr), shape=(len(rows), n_genes))
assert np.allclose(data, np.round(data)), "X is not integer counts"
print(f"loaded {X.shape} with {X.nnz} nonzeros in {time.time() - t0:.1f}s")

cells = sel.loc[rows]
cells["condition"] = cells.cytokine.map({TEST: "IFNb", REF: "PBS"})
cells["pb"] = cells.donor.astype(str) + "_" + cells.condition.astype(str)

# pseudobulk: sum raw counts per donor x condition
pb_ids = sorted(cells.pb.unique())
pb_index = {p: k for k, p in enumerate(pb_ids)}
M = sp.csr_matrix(
    (np.ones(len(cells)), (cells.pb.map(pb_index).values, np.arange(len(cells)))),
    shape=(len(pb_ids), len(cells)),
)
pb_counts = np.asarray((M @ X).todense()).round().astype(np.int64)
counts = pd.DataFrame(pb_counts, index=pb_ids, columns=var_names)
meta = pd.DataFrame(
    {
        "donor": [p.rsplit("_", 1)[0] for p in pb_ids],
        "condition": [p.rsplit("_", 1)[1] for p in pb_ids],
        "n_cells": cells.pb.value_counts().reindex(pb_ids).values,
    },
    index=pb_ids,
)
print(meta)
print("library sizes:\n", counts.sum(axis=1))

counts.to_csv(os.path.join(WS, "pseudobulk_counts_all_genes.csv"))
meta.to_csv(os.path.join(WS, "pseudobulk_meta.csv"))

# 4. gene filter: >= 10 total counts across all pseudobulk samples
gene_tot = counts.sum(axis=0)
counts = counts.loc[:, gene_tot >= MIN_GENE_COUNTS]
print(f"genes kept with >= {MIN_GENE_COUNTS} total counts: {counts.shape[1]} of {n_genes}")

# 5. pyDESeq2, paired design
dds = DeseqDataSet(counts=counts, metadata=meta, design="~ donor + condition", quiet=True, n_cpus=4)
dds.deseq2()
st = DeseqStats(dds, contrast=["condition", "IFNb", "PBS"], alpha=PADJ_CUT, quiet=True, n_cpus=4)
st.summary()
res = st.results_df.sort_values("padj")
res.to_csv(os.path.join(WS, "deseq2_results_cd14_ifnb_vs_pbs.csv"))

# 6. DEG count
sig = res[(res.padj < PADJ_CUT) & (res.log2FoldChange.abs() > LFC_CUT)]
n_degs = int(sig.shape[0])
print(f"\nDEGs (padj < {PADJ_CUT}, |log2FC| > {LFC_CUT}): {n_degs}  "
      f"(up {int((sig.log2FoldChange > 0).sum())}, down {int((sig.log2FoldChange < 0).sum())})")
print("top 25 by padj:\n", sig.head(25)[["baseMean", "log2FoldChange", "padj"]])

# secondary: unpaired design for reference only
dds2 = DeseqDataSet(counts=counts, metadata=meta, design="~ condition", quiet=True, n_cpus=4)
dds2.deseq2()
st2 = DeseqStats(dds2, contrast=["condition", "IFNb", "PBS"], alpha=PADJ_CUT, quiet=True, n_cpus=4)
st2.summary()
res2 = st2.results_df
n_degs_unpaired = int(((res2.padj < PADJ_CUT) & (res2.log2FoldChange.abs() > LFC_CUT)).sum())
print(f"reference only, unpaired design ~ condition: {n_degs_unpaired} DEGs")

with open(os.path.join(WS, "eval_answer.json"), "w") as fh:
    json.dump({"n_degs": n_degs}, fh)
with open(os.path.join(WS, "run_summary.json"), "w") as fh:
    json.dump(
        {
            "n_degs_paired_design": n_degs,
            "n_degs_unpaired_design_reference": n_degs_unpaired,
            "kept_donors": keep_donors,
            "dropped_donors": drop_donors,
            "n_cells": int(len(cells)),
            "n_genes_tested": int(counts.shape[1]),
            "design": "~ donor + condition",
            "pydeseq2": "0.6.0",
        },
        fh,
        indent=2,
    )
print(f"done in {time.time() - t0:.1f}s")
