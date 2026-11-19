"""Re-run the DESeq2 step with pydeseq2 0.5.2 (the version the env shipped with),
patched only where pandas 3 rejects boolean flags written into float NaN columns."""
import os, re, shutil, sys, importlib.metadata
import numpy as np, pandas as pd

WS = "/workspace"
SP = "/scratch"
src, root = os.path.join(SP, "pydeseq2_0.5.2"), os.path.join(SP, "py052")
dst = os.path.join(root, "pydeseq2")
if not os.path.exists(dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    p = os.path.join(dst, "dds.py"); s = open(p).read()
    s, n = re.subn(r'(self\.var\["_(?:genewise|MAP|LFC)_converged"\] = np\.full\(self\.n_vars, np\.nan)\)', r"\1, dtype=object)", s)
    assert n == 3, n
    open(p, "w").write(s)
sys.path.insert(0, root)
import pydeseq2
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
assert pydeseq2.__file__.startswith(root), pydeseq2.__file__
print("pydeseq2 source:", pydeseq2.__file__, "| version file:", open(os.path.join(dst, "__version__.py")).read().strip())

counts = pd.read_csv(os.path.join(WS, "pseudobulk_counts_all_genes.csv"), index_col=0)
meta = pd.read_csv(os.path.join(WS, "pseudobulk_meta.csv"), index_col=0)
counts = counts.loc[:, counts.sum(axis=0) >= 10]
print("samples", counts.shape[0], "genes", counts.shape[1])
new = pd.read_csv(os.path.join(WS, "deseq2_results_cd14_ifnb_vs_pbs.csv"), index_col=0)
new_sig = set(new.index[(new.padj < 0.05) & (new.log2FoldChange.abs() > 1)])
for design in ["~ donor + condition", "~ condition"]:
    dds = DeseqDataSet(counts=counts, metadata=meta, design=design, quiet=True, n_cpus=4)
    dds.deseq2()
    st = DeseqStats(dds, contrast=["condition", "IFNb", "PBS"], alpha=0.05, quiet=True, n_cpus=4)
    st.summary()
    r = st.results_df
    sig = set(r.index[(r.padj < 0.05) & (r.log2FoldChange.abs() > 1)])
    print(f"[0.5.2] design {design!r}: {len(sig)} DEGs (padj<0.05, |LFC|>1); padj<0.05 only: {(r.padj<0.05).sum()}")
    if design == "~ donor + condition":
        print(f"   overlap with 0.6.0 paired DEG set ({len(new_sig)}): {len(sig & new_sig)}; only 0.5.2: {len(sig - new_sig)}; only 0.6.0: {len(new_sig - sig)}")
        m = r.join(new, lsuffix="_052", rsuffix="_060")
        print("   max |LFC diff|:", (m.log2FoldChange_052 - m.log2FoldChange_060).abs().max(),
              "| max |padj diff|:", (m.padj_052 - m.padj_060).abs().max())
        r.to_csv(os.path.join(WS, "deseq2_results_cd14_ifnb_vs_pbs_pydeseq2_0.5.2.csv"))
