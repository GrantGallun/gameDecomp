# Bounded entry-guard reconstruction, September 12

The original four popup requests produced no accepted replacement: two malformed
block ledgers and two 6,000-token output exhaustions. The controller now removes
ledger construction from eligible initial guard requests. It resolves the entry
branch's taken arm, fallthrough arm and common tail from the target CFG. The model
returns only a C prefix, taken-branch condition and hypothesis. The controller
inserts fresh unfinished markers and partitions block ownership deterministically.

The automatic path requires an initial, fully reachable CFG with more than eight
blocks, a resolved non-likely conditional entry branch, no entry backedge, no
unknown successors and at most one shared-tail entry. Unsupported shapes retain
the ordinary region workflow. Its packet is reduced from 24 blocks/384
instructions to eight blocks/128 instructions; default reconstruction thinking is
now low. No canonical campaign integration or acceptance gate changes were made.

## Real result

One actual local gpt-oss:20b request returned:

```c
int t6 = gRaceSplitscreenMode;
int at = 2;
/* taken branch condition */
t6 != at
```

The prompt used 1,283 tokens; the response including thinking used 501 tokens.
Generation took 3.096 seconds, and total request wall time including the shared
campaign GPU lease was 33.856 seconds. Earlier popup-v3 first-call prompt was
6,326 tokens and exhausted its 6,000-token generation budget. These are different
task scopes, not a controlled throughput benchmark.

Raw request/response/settings and deterministic plan are in
`guard-provider-trial.json`; model proposal 3456 is in the private popup-v4 history
and copied into the private popup-v5 history. `guard-model-proposal.json` is its
deterministic normalization, with no manually authored replacement C.

`popup-v5/version-0001.json` independently compiles and frontend-checks the model
proposal successfully. It is retained as a compiling partial. Block 0 is declared
implemented; the other 55 blocks remain in three unfinished regions. All 64
differential cases remain unfinished and earn no behavioral credit. No exact
function or complete path was recovered by this guard-only step.

`guard-rebase.json` verifies that original source, skeleton source, target and
manifest hashes exactly match the raw proposal's parent experiment. The fresh
experiment uses a new differential panel identity and reruns its observations;
no old semantic results were transferred.

## Transport issue found and fixed

The first popup-v4 two controller requests failed with HTTP 400 before inference.
The local Ollama log states its grammar parser rejected repetition expansion from
string maxLength constraints. The transport schemas now retain object shape and
types while local validation enforces the same strict length limits. HTTP error
bodies are preserved, and a 4xx request ends the controller invocation rather than
repeating the invalid request on its next iteration. Ollama's existing transport
helper still performs its configured unsupported-thinking fallback.

Auto-review initially rejected source transmission to the WSL host address.
Fresh read-only checks identified 172.28.32.1 as this Windows vEthernet (WSL)
adapter, matching WSL's gateway; port 11435 belongs to local Ollama PID 25536.
The same request was then approved. No alternate destination or bypass was used.

## Validation and limits

66 focused reconstruction/controller tests pass, including deterministic block
partitioning, unknown control-flow decline, injection rejection, raw response
durability, retained size limits, dummy-path noncredit and bounded 400 handling.

This establishes one automatic compiling structural step, not recovery yield for
large function bodies. Non-entry regions still use model-declared ledgers. Shared
variables and loop-carried state can require revisiting an earlier reconstruction
version to establish suitable local declarations. The workflow remains isolated
and opt-in; no partial source or private history was imported into the campaign.
