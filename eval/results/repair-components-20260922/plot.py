"""Static interaction figure from audited measurements, not fitted predictions."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent
report = json.loads((OUT / "analysis.json").read_text())
rows = {r["mask"]: r for r in report["factorial_cells"]}
order = [0, 1, 2, 3]
labels = ["Neither", "Pointer-to-link\nonly (P)", "Node pointer\nonly (N)", "Both (P + N)"]
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
fig, (ax, effect) = plt.subplots(1, 2, figsize=(13, 5.5), gridspec_kw={"width_ratios": [1.5, 1]})
fig.patch.set_facecolor("#fafbfc")
for axis in (ax,effect):
    axis.set_facecolor("#fafbfc")
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="y", alpha=.15)
    axis.set_axisbelow(True)
for shift, alias, color, label in ((-.18, 0, "#8093a8", "Without address reuse"), (.18, 4, "#217a9b", "With address reuse (A)")):
    values = [rows[m|alias]["score"] for m in order]
    bars = ax.bar([i+shift for i in range(4)], values, width=.34, color=color, label=label)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x()+bar.get_width()/2, value+1.1, f"{value:.3f}", ha="center", fontsize=8.5)
ax.set_xticks(range(4), labels)
ax.set_ylim(0, 119)
ax.set_ylabel("Normalized similarity score (diagnostic)")
ax.set_xlabel("Register declarations enabled")
ax.legend(loc="upper left", frameon=False, fontsize=9)
ax.annotate("Only this combination is certified exact", xy=(3.18,100), xytext=(1.55,113),
            fontsize=9, color="#155f3d", arrowprops={"arrowstyle":"->", "color":"#155f3d"})
delta = [rows[m|4]["score"]-rows[m]["score"] for m in order]
bars = effect.bar(range(4), delta, color=["#9da7af", "#217a9b", "#b85a48", "#237b55"], width=.55)
effect.axhline(0, color="#666", linewidth=.7)
for bar, value in zip(bars, delta):
    effect.text(bar.get_x()+bar.get_width()/2, value + (.2 if value >= 0 else -.25), f"{value:+.3f}",
                ha="center", va="bottom" if value >=0 else "top", fontsize=10)
effect.set_xticks(range(4), ["Neither", "P only", "N only", "P + N"])
effect.set_ylim(-4.5, 8.5)
effect.set_ylabel("Change from adding address reuse (A)")
effect.set_xlabel("Other components already enabled")
effect.set_title("Small score effect can finish the repair", fontsize=12, pad=12)
fig.suptitle("Repair components interact: measured on __osDequeueThread", fontsize=16, x=.05, ha="left")
fig.text(.05,.02,"8 controlled combinations · 1 exposed function · 2 new compiler calls · no generalization or significance claim", fontsize=10, color="#56616d")
fig.tight_layout(rect=[0,.06,1,.93])
fig.savefig(OUT / "interactions.png", dpi=160)
fig.savefig(OUT / "interactions.svg")
