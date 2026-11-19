"""Per-cell sanity check of top hits and ISGs in the selected CD14 monocytes."""
import h5py, numpy as np, pandas as pd, scipy.sparse as sp
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 30)
H5 = "/workspace/data/Parse_10M_PBMC_100K_subset.h5ad"
f = h5py.File(H5, "r")
def cat(k):
    g = f["obs"][k]; c = np.array([x.decode() if isinstance(x, bytes) else x for x in g["categories"][:]], dtype=object)
    return c[g["codes"][:]]
obs = pd.DataFrame({k: cat(k) for k in ["cell_type", "cytokine", "donor"]})
obs["tscp"] = f["obs"]["tscp_count"][:]; obs["ngenes"] = f["obs"]["gene_count"][:]; obs["pct_mt"] = f["obs"]["pct_counts_MT"][:]
keep = ["Donor1","Donor10","Donor11","Donor12","Donor2","Donor4","Donor5","Donor6","Donor8","Donor9"]
sel = obs[(obs.cell_type == "CD14 Mono") & obs.cytokine.isin(["IFN-beta", "PBS"]) & obs.donor.isin(keep)]
rows = np.sort(sel.index.values)
indptr = f["X"]["indptr"][:]; var = np.array([x.decode() if isinstance(x, bytes) else x for x in f["var"]["_index"][:]], dtype=object)
d, ix = [], []
for r in rows:
    a, b = indptr[r], indptr[r + 1]; d.append(f["X"]["data"][a:b]); ix.append(f["X"]["indices"][a:b])
X = sp.csr_matrix((np.concatenate(d), np.concatenate(ix), np.concatenate([[0], np.cumsum([len(x) for x in d])])), shape=(len(rows), len(var)))
sel = sel.loc[rows]
print("per-cell QC by condition:"); print(sel.groupby("cytokine")[["tscp", "ngenes", "pct_mt"]].describe().T.round(1))
genes = ["ENSG00000224228","HFM1","TRAF5","RXFP1","BRIP1","PRLR","CCR7","CYP19A1","MRC1","PID1","MMP9","LYZ","CD14",
         "ISG15","IFIT1","IFIT2","RSAD2","CXCL10","CXCL11","MX1","STAT1","IFI6","IFI27","S100A8","CD3E","MS4A1","NKG7"]
gi = {g: np.where(var == g)[0][0] for g in genes}
cp10k = sp.diags(1e4 / np.asarray(X.sum(axis=1)).ravel()) @ X
out = []
for g in genes:
    col = X[:, gi[g]].toarray().ravel(); ncol = cp10k[:, gi[g]].toarray().ravel()
    rec = {"gene": g}
    for cond in ["PBS", "IFN-beta"]:
        m = (sel.cytokine == cond).values
        rec[f"{cond}_frac_expr"] = round(float((col[m] > 0).mean()), 3)
        rec[f"{cond}_mean_cp10k"] = round(float(ncol[m].mean()), 2)
        rec[f"{cond}_max_count"] = int(col[m].max())
    out.append(rec)
print(pd.DataFrame(out).set_index("gene"))
# per-donor consistency for a few genes (pseudobulk CPM)
pb = sel.assign(k=sel.donor + "_" + sel.cytokine.map({"IFN-beta": "IFNb", "PBS": "PBS"}))
M = sp.csr_matrix((np.ones(len(pb)), (pd.factorize(pb.k)[0], np.arange(len(pb)))))
labels = pd.factorize(pb.k)[1]
P = np.asarray((M @ X).todense()); P = P / P.sum(axis=1, keepdims=True) * 1e6
tab = pd.DataFrame({g: P[:, gi[g]] for g in ["ENSG00000224228","MRC1","ISG15","CXCL11","CCR7","LYZ","STAT1"]}, index=labels).round(0)
tab["cond"] = [k.split("_")[1] for k in labels]; tab["donor"] = [k.split("_")[0] for k in labels]
print("\nper-donor pseudobulk CPM:"); print(tab.sort_values(["cond", "donor"]))
