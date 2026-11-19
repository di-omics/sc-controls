"""IFN-beta vs PBS pseudobulk DE in CD14 monocytes (pyDESeq2).

Steps
1. Read obs metadata with h5py (no full matrix load).
2. Select CD14 Mono cells treated with IFN-beta or PBS.
3. Drop donors with < 5 cells in either condition.
4. Read only the selected rows of the CSR count matrix, sum raw counts
   per donor x condition (pseudobulk).
5. Keep genes with >= 10 total counts across all pseudobulk samples.
6. pyDESeq2, design ~ donor + condition (donor-paired), contrast IFN-beta vs PBS.
7. Count DEGs at padj < 0.05 and |log2FC| > 1; write eval_answer.json.
"""
import json
import sys

import h5py
import numpy as np
import pandas as pd
import scipy.sparse as sp
from pydeseq2.dds import DeseqDataSet
from pydeseq2.default_inference import DefaultInference
from pydeseq2.ds import DeseqStats

H5 = "data/Parse_10M_PBMC_100K_subset.h5ad"
CELL_TYPE = "CD14 Mono"
STIM, CTRL = "IFN-beta", "PBS"
MIN_CELLS = 5
MIN_GENE_COUNTS = 10
PADJ, LFC = 0.05, 1.0

f = h5py.File(H5, "r")


def cat(name):
    g = f["obs"][name]
    return pd.Categorical.from_codes(g["codes"][:], g["categories"][:].astype(str))


obs = pd.DataFrame({k: cat(k) for k in ["cell_type", "cytokine", "donor"]})
obs.index = f["obs/_index"][:].astype(str)
genes = f["var/_index"][:].astype(str)
n_genes = len(genes)

sel = obs[(obs.cell_type == CELL_TYPE) & (obs.cytokine.isin([STIM, CTRL]))].copy()
sel["donor"] = sel["donor"].astype(str)
sel["condition"] = sel["cytokine"].astype(str)

tab = pd.crosstab(sel.donor, sel.condition)
print("cells per donor x condition (before donor filter):")
print(tab.to_string())
keep_donors = tab.index[(tab[STIM] >= MIN_CELLS) & (tab[CTRL] >= MIN_CELLS)].tolist()
drop_donors = sorted(set(tab.index) - set(keep_donors))
print(f"\nkept donors ({len(keep_donors)}): {keep_donors}")
print(f"dropped donors ({len(drop_donors)}): {drop_donors}")
sel = sel[sel.donor.isin(keep_donors)]
print(f"cells used: {len(sel)}")

# ---- read only the selected rows from the CSR matrix ----
row_idx = np.flatnonzero(
    (obs.cell_type == CELL_TYPE).values
    & obs.cytokine.isin([STIM, CTRL]).values
    & obs.donor.astype(str).isin(keep_donors).values
)
assert len(row_idx) == len(sel)
indptr = f["X/indptr"][:]
Xd, Xi = f["X/data"], f["X/indices"]
data_parts, ind_parts, new_indptr = [], [], [0]
# group contiguous runs of rows to limit the number of HDF5 reads
breaks = np.flatnonzero(np.diff(row_idx) != 1) + 1
for run in np.split(row_idx, breaks):
    a, b = indptr[run[0]], indptr[run[-1] + 1]
    d = Xd[a:b]
    i = Xi[a:b]
    data_parts.append(d)
    ind_parts.append(i)
    local = indptr[run[0] : run[-1] + 2] - a
    new_indptr.extend((local[1:] + new_indptr[-1]).tolist())
data = np.concatenate(data_parts)
indices = np.concatenate(ind_parts)
X = sp.csr_matrix((data, indices, np.asarray(new_indptr)), shape=(len(row_idx), n_genes))
assert np.all(data == np.round(data)), "X is not integer counts"
print(f"loaded sparse counts: {X.shape}, nnz={X.nnz}, max={data.max():.0f}")

# ---- pseudobulk: sum raw counts per donor x condition ----
sel["sample_id"] = sel["donor"] + "_" + sel["condition"]
sample_ids = sorted(sel["sample_id"].unique())
codes = pd.Categorical(sel["sample_id"], categories=sample_ids).codes
agg = sp.csr_matrix(
    (np.ones(len(codes)), (codes, np.arange(len(codes)))), shape=(len(sample_ids), len(codes))
)
pb = np.asarray((agg @ X).todense()).astype(np.int64)
counts = pd.DataFrame(pb, index=sample_ids, columns=genes)
meta = pd.DataFrame(
    {
        "donor": [s.rsplit("_", 1)[0] for s in sample_ids],
        "condition": [s.rsplit("_", 1)[1] for s in sample_ids],
        "n_cells": sel.groupby("sample_id").size().reindex(sample_ids).values,
    },
    index=sample_ids,
)
# clean level names for the formula interface (no hyphens)
meta["condition"] = meta["condition"].map({STIM: "IFNb", CTRL: "PBS"})
meta["condition"] = pd.Categorical(meta["condition"], categories=["PBS", "IFNb"])
print("\npseudobulk samples:")
print(meta.assign(lib_size=counts.sum(axis=1).values).to_string())

# ---- gene filter: >= 10 total counts across all samples ----
gene_keep = counts.sum(axis=0) >= MIN_GENE_COUNTS
counts = counts.loc[:, gene_keep]
print(f"\ngenes kept with >= {MIN_GENE_COUNTS} total counts: {counts.shape[1]} of {n_genes}")
counts.T.to_csv("pseudobulk_counts.csv")
meta.to_csv("pseudobulk_metadata.csv")


def run_deseq(design, tag):
    inf = DefaultInference(n_cpus=4)
    dds = DeseqDataSet(counts=counts, metadata=meta, design=design, refit_cooks=True, inference=inf, quiet=True)
    dds.deseq2()
    print(f"\n[{tag}] design matrix columns: {list(dds.obsm['design_matrix'].columns)}")
    st = DeseqStats(dds, contrast=["condition", "IFNb", "PBS"], alpha=PADJ, cooks_filter=True,
                    independent_filter=True, inference=inf, quiet=True)
    st.summary()
    res = st.results_df.copy()
    res.to_csv(f"deseq2_results_{tag}.csv")
    sig = res[(res.padj < PADJ) & (res.log2FoldChange.abs() > LFC)]
    print(f"[{tag}] DEGs padj<{PADJ} & |log2FC|>{LFC}: {len(sig)} "
          f"(up {int((sig.log2FoldChange > 0).sum())}, down {int((sig.log2FoldChange < 0).sum())}); "
          f"padj<{PADJ} alone: {int((res.padj < PADJ).sum())}; genes tested (padj not NA): {int(res.padj.notna().sum())}")
    print(f"[{tag}] top 10 by padj:")
    print(sig.sort_values("padj").head(10)[["baseMean", "log2FoldChange", "pvalue", "padj"]].to_string())
    return len(sig)


n_paired = run_deseq("~ donor + condition", "paired")
n_unpaired = run_deseq("~ condition", "unpaired")

answer = {"n_degs": int(n_paired)}
with open("eval_answer.json", "w") as fh:
    json.dump(answer, fh)
with open("de_summary.json", "w") as fh:
    json.dump({"primary_design": "~ donor + condition", "n_degs_paired": int(n_paired),
               "n_degs_unpaired": int(n_unpaired), "kept_donors": keep_donors,
               "dropped_donors": drop_donors, "n_cells": int(len(sel)),
               "n_genes_tested": int(counts.shape[1])}, fh, indent=2)
print("\nANSWER:", answer)
