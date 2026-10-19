import json, sys, statistics as s
d = json.load(sys.stdin); print(d.get("as_of"), len(d.get("rows", [])), d.get("detail"), d.get("references"))
rows = d["rows"]
g = lambda leg, p: (leg or {}).get("change", {}).get(p)
for r in rows:
    if r["kind"] != "stock":
        print(f'{r["underlying"]:16s} spot {r["spot_change"]["1D"]}  CE {g(r.get("ce"), "1D")}  PE {g(r.get("pe"), "1D")}   week spot {r["spot_change"]["1W"]}')
st = sorted([r for r in rows if r["kind"] == "stock" and r["spot_change"]["1D"] is not None], key=lambda r: r["spot_change"]["1D"])
print("worst", [(r["underlying"], r["spot_change"]["1D"]) for r in st[:6]])
print("best", [(r["underlying"], r["spot_change"]["1D"]) for r in st[-6:]])
print("stocks up", sum(r["spot_change"]["1D"] > 0 for r in st), "of", len(st))
ce = [g(r.get("ce"), "1D") for r in st]; pe = [g(r.get("pe"), "1D") for r in st]
print("median ATM CE", s.median([x for x in ce if x is not None]), "PE", s.median([x for x in pe if x is not None]))
