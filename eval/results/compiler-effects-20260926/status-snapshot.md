# Status refresh before recovery (checkpoint 29289)

Command run successfully in native WSL:

```text
python -m eval.status --db /home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite --build-tree /home/grant/decomp/sbk1 --results /mnt/c/Code/gameDecomp/eval/results
```

The canonical ledger/build-tree report counted 990 byte-exact functions of
2,013 attempted: 780 SOLVED, 109 reconstructed-header-assisted, 101
reference-type-assisted, zero recovered-from-target-source in that breakdown.
It reported 179,134 attempts, 72,041 evidence rows and zero inference rows.
The separate function-exact artifact scan counted 12. The tool also reported
12 verified files absent from the attempts table. Its test-discovery row was
zero; focused test execution receipts in this experiment are the verification
evidence, not that discovery row.

Campaign checkpoint metadata uses a different operational scope: 998
object-exact/integrated nodes, including 21 integrated, and 24 function-exact
pending integration, in a 2,051-function cohort. These counts are not
interchangeable with the ledger/build-tree status report. Both pause markers
were present, with three interrupted jobs pending recovery. Private experiment
results are outside these live counts until explicitly accepted.
