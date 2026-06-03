# Split-Graph dERN and ERN Pipeline Optimization Notes

> **Note on paths.** This document predates the repository reorganization and
> refers to the original file locations. They now map as follows: the C++ sources
> (`dern.cpp`, `bip2split.cpp`, `small_graph.hpp`) live in `cpp/`; the shell
> scripts and `dern_parallel.py` live in `scripts/`; the compiled binaries are
> built into `cpp/bin/`; and the ERN pipeline `split_edge_reconstruction.py` is
> now the `ern` Python package (`src/ern/`), invoked as `python -m ern`. The
> optimization rationale below is unchanged.

This document explains, in plain English and in technical terms, how the two pipelines in this repository were optimized.

The two pipelines are:

1. The `dERN` pipeline in `dern/`, centered around `dern.cpp`.
2. The `ERN` pipeline in `split_edge_reconstruction.py`.

The goal of this write-up is not just to list tricks. It is to explain why each trick helps, what exact cost it removes, and what tradeoff comes with it.

## 1. What Problem Are These Pipelines Solving?

### 1.1 Plain-English version

Take a graph and imagine deleting one edge at a time. Each deleted-edge result is like a "card" in a deck. If you know enough of those cards, maybe you can work out which original graph they came from.

`ERN` asks:

"How many edge-deleted cards do I need before the original graph is forced?"

`dERN` asks a slightly easier question:

"How many edge-deleted cards do I need if each card also tells me one extra piece of degree information about the removed edge?"

That extra degree information is powerful. It removes a lot of ambiguity. That is one reason the `dERN` pipeline can be much faster.

### 1.2 Technical version

For a graph `G`, its edge deck is the multiset of graphs obtained by deleting each edge once. The edge reconstruction number is the minimum size `k` of a submultiset of that deck that uniquely determines `G` up to isomorphism.

For `dERN`, each edge card also carries a degree-associated label. In `dern.cpp`, that label is stored as:

`d_uv = deg_G(u) + deg_G(v) - 2`

When the edge `uv` is removed, this equals:

`deg_{G-uv}(u) + deg_{G-uv}(v)`

So each `dERN` card is richer than an ordinary `ERN` card.

## 2. Why dERN and ERN Behave So Differently

### 2.1 The short answer

The `dERN` pipeline is faster because it benefits from three major structural advantages:

1. It generates only split graphs instead of generating all unlabeled graphs and filtering later.
2. It solves the hard part in compiled C++ instead of Python.
3. The extra degree label makes candidate reconstruction much more constrained.

The original `ERN` pipeline lacked all three of those advantages at once.

### 2.2 The deeper reason

In `dERN`, a card tells you both:

1. what the deleted graph looks like, and
2. what the endpoint degree sum must be.

That means when you try to rebuild the parent graph from a card, you do not have to try every possible nonedge. You only try nonedges whose endpoint degrees match the required value. That sharply cuts the candidate set.

In ordinary `ERN`, the card is just the deleted graph. No degree tag survives. So many more parent graphs remain possible. That creates larger blocker sets, more deck comparisons, and more expensive exact searches for `k >= 3`.

## 3. File Map

### 3.1 dERN files

`dern/small_graph.hpp`

This is the low-level graph representation and graph6 conversion layer.

`dern/dern.cpp`

This is the main compiled `dERN` solver.

`dern/bip2split.cpp`

This turns bipartite graphs into split graphs by making one side a clique.

`dern/gen_split_n.sh`

This generates split graphs directly.

`dern/dern_parallel.py`

This fans one graph stream out across multiple `bin/dern` worker processes.

`dern/run_split_dern_n.sh`

Single-worker shell wrapper.

`dern/run_split_dern_n_parallel.sh`

Multi-worker shell wrapper.

### 3.2 ERN files

`split_edge_reconstruction.py`

This now contains two exact ERN modes:

1. `global` mode: the original exhaustive all-graph approach.
2. `local` mode: the newer split-target exact solver added during this session.

`dern/run_split_ern_n_parallel.sh`

This is the new wrapper for the faster local ERN path.

## 4. Shared Optimization Philosophy

Both pipelines try to avoid the same kinds of waste:

1. Do not generate graphs you will later throw away.
2. Do not compare graphs in raw labeled form if isomorphism is the real question.
3. Do not canonicalize one graph at a time if you can batch them.
4. Do not keep recomputing the same card or candidate family.
5. Do not do expensive exact search before ruling out easy cases.
6. Do not serialize independent work if it can be chunked and parallelized.

Everything in the code is some version of one of those six ideas.

## 5. dERN Pipeline Optimizations

## 5.1 Direct split-graph generation instead of generate-everything-then-filter

### Plain-English version

If your assignment is "study split graphs," it is wasteful to generate every graph in the world and then ask which ones are split.

The `dERN` pipeline does something smarter. It builds split graphs directly from bipartite graphs.

### Technical version

`dern/gen_split_n.sh` uses `genbg` or `nauty-genbg` to generate bipartite graphs for every partition size `k` and `n-k`. Then `dern/bin/bip2split` turns the first side into a clique. That construction produces split graphs directly.

This is much cheaper than:

1. generating all unlabeled graphs on `n` vertices with `geng`,
2. decoding each graph,
3. testing whether it is split,
4. discarding the large majority that are not.

### Why this matters

This is one of the biggest optimizations in the whole project. It changes the size of the input universe before the solver even starts.

## 5.2 Deduplicating split graphs with `shortg`

### Plain-English version

Different bipartite inputs can lead to isomorphic split graphs after the clique side is added. If you do not deduplicate them, you solve the same graph again and again.

### Technical version

`dern/gen_split_n.sh` pipes the generated split graphs through `shortg` or `nauty-shortg`, unless `NO_SHORTG` is set.

That means the downstream solver sees one canonical representative per isomorphism class, not duplicates.

### Why this matters

It removes duplicate work before any expensive solving begins. This is exact deduplication, not a heuristic.

### Tradeoff

`shortg` itself costs time and temporary space. For exact counting over isomorphism classes, it is worth it. For rough throughput experiments where duplicates are acceptable, `NO_SHORTG=1` can be used, but then the results are no longer "one unlabeled split graph each."

## 5.3 Compact fixed-size graph representation

### Plain-English version

Small graphs do not need heavy data structures. If a graph has at most 16 vertices, you can store each row of its adjacency matrix in one 16-bit integer. That makes edge checks very cheap.

### Technical version

`dern/small_graph.hpp` defines:

1. `Graph16`, with `adj` as `std::array<uint16_t, kMaxN>`.
2. `GraphKey`, which packs the upper triangle into two 64-bit integers.

Benefits:

1. edge existence is a bit test,
2. edge insertion and deletion are bitwise operations,
3. degree counts use popcount,
4. graph hashing and equality are cheap,
5. no per-graph heap allocation is needed.

### Why this matters

This is the kind of optimization that saves time everywhere, not just in one hotspot. Every edge test, degree computation, hash lookup, and copy gets cheaper.

## 5.4 Fast graph6 parsing and encoding

### Plain-English version

If your input and output are graph6 strings, you do not want to constantly convert through slow general-purpose formats.

### Technical version

`ParseGraph6Line` and `ToGraph6` in `dern/small_graph.hpp` do direct graph6 decoding and encoding. They avoid extra dependencies and are tailored to the small-graph fixed-size representation.

### Why this matters

These functions sit on the boundary of the pipeline. Any inefficiency here multiplies across every graph processed.

## 5.5 Canonicalization with nauty

### Plain-English version

Two graphs may look different only because the vertices were renamed. Canonicalization gives them one standard form so equivalent graphs become literally identical.

### Technical version

`Canonicalize` in `dern.cpp` converts `Graph16` into a nauty dense graph, runs `densenauty`, and converts the canonical output back into `Graph16` and `GraphKey`.

The function also performs `nauty_check` only once using a static guard.

### Why this matters

Without canonicalization, every comparison would be polluted by labeling issues. With canonicalization, equality and hashing on `GraphKey` become valid isomorphism tests.

### Tradeoff

Canonicalization is expensive. The rest of the pipeline exists to minimize how often it must be called.

## 5.6 Using richer cards in dERN: `CardKey = (deleted graph, degree label)`

### Plain-English version

If two deleted-edge cards look the same but came from edges with different degree behavior, `dERN` treats them as different cards. That extra label preserves information that ERN throws away.

### Technical version

`CardKey` stores:

1. `h_key`: the canonical deleted graph,
2. `d`: the degree-associated label.

`CardKeyHash` hashes both pieces together.

This means candidate generation is constrained by a true invariant of the deleted edge, not by guesswork.

### Why this matters

This is not just an optimization. It changes the problem into an easier one.

## 5.7 Exact candidate generation from one dERN card

### Plain-English version

Given one deleted card and its degree label, the solver does not try every possible way to add an edge back. It only tries the ways that match the degree rule.

### Technical version

`GenerateCandidateGraphs` reconstructs all possible parents of a card by scanning nonedges `xy` in the deleted graph `H` and checking:

`deg_H(x) + deg_H(y) == d`

Only if that condition holds does it add `xy`, canonicalize the result, and keep it.

This is exact, not heuristic. If the deleted edge had label `d`, then the restored endpoints in the deleted graph must satisfy that equality.

### Why this matters

This is one of the key reasons `dERN` scales better than `ERN`.

## 5.8 Deduplicating candidate parents immediately

### Plain-English version

Different edge insertions can still lead to the same unlabeled parent graph. Solving the same parent twice is wasted effort.

### Technical version

`GenerateCandidateGraphs` sorts the candidate `GraphKey` values and runs `std::unique`.

### Why this matters

All later set operations become smaller.

## 5.9 Early exit for trivial edgeless graphs

### Plain-English version

If a graph has no edges, there is nothing to reconstruct.

### Technical version

`dern.cpp` checks `m_edges == 0` immediately and returns `dERN = 0`.

### Why this matters

Cheap cases should not enter the expensive machinery.

## 5.10 Build unique card types once, count multiplicities

### Plain-English version

If the same card type appears several times, you do not want to recompute its candidate parent set each time. You want to compute it once and remember how many copies occurred.

### Technical version

The solver uses:

1. `index_by_card` to map each `CardKey` to a unique slot,
2. `infos` to store one `CardInfo` per distinct card type,
3. `multiplicity_in_g` to count repeats.

### Why this matters

This converts repeated expensive work into one expensive call plus cheap counting.

## 5.11 Early `dERN = 1` detection

### Plain-English version

If one card can only come from one graph, you are done.

### Technical version

As each unique card type is created, the solver calls `GenerateCandidateGraphs`. If the returned candidate list is exactly `{G}`, then `dERN = 1` and the solver exits early.

### Why this matters

This avoids pairwise checks and multiplicity checks for the many easy graphs.

## 5.12 Sorting card types by smallest candidate set first

### Plain-English version

If you are looking for a pair of cards that uniquely identifies the graph, start with the most informative cards, not the vaguest ones.

### Technical version

For the `dERN = 2` search using two distinct card types, the solver sorts `infos` by `candidates.size()` ascending.

### Why this matters

Small candidate lists intersect faster and are more likely to isolate the graph quickly.

## 5.13 Intersection without materializing big sets

### Plain-English version

If two candidate lists are sorted, you can compare them with two pointers instead of building heavy set objects.

### Technical version

`IntersectionIsExactlyG` walks two sorted candidate vectors directly. It checks whether their intersection is exactly the original graph.

### Why this matters

This saves both memory and CPU time.

## 5.14 Special handling for "two copies of the same card" in `dERN = 2`

### Plain-English version

Sometimes two different card types are not needed. Two copies of one repeated card type may already be enough.

### Technical version

If a card type appears with multiplicity at least 2 in `G`, the solver checks whether any competing candidate graph also contains that card type at multiplicity at least 2. If none do, then `dERN = 2`.

This check is delayed until after the faster two-distinct-card test.

### Why this matters

It covers an important exact case without paying that cost for every graph up front.

## 5.15 Streaming input instead of loading everything

### Plain-English version

The solver processes one graph at a time from standard input. It does not first build a giant list of all graphs.

### Technical version

`while (std::getline(std::cin, line))` in `dern.cpp` is a streaming loop.

### Why this matters

Streaming keeps memory usage stable even for large jobs.

## 5.16 Pre-reserving container sizes

### Plain-English version

If you know roughly how large a container will get, reserve that space once instead of repeatedly growing it.

### Technical version

`index_by_card.reserve(m_edges * 2)` and `infos.reserve(m_edges)` are examples.

### Why this matters

This reduces allocator churn in tight loops.

## 5.17 The `dERN` parallel fan-out wrapper

### Plain-English version

The Python script `dern_parallel.py` does not solve graphs itself. It acts like a dispatcher. It reads one graph stream and spreads the graphs across several independent solver processes.

### Technical version

`dern_parallel.py`:

1. launches `jobs` copies of `bin/dern`,
2. sends input lines round-robin across workers,
3. writes each worker's CSV output to a part file,
4. reads worker stderr in background threads,
5. merges output part files at the end,
6. merges summary counters reported by the workers.

### Why this matters

This converts a single-core compiled solver into a multi-core batch system with almost no change to solver logic.

### Why processes, not Python threads

Each worker is an external compiled program. Running several of them in parallel uses multiple CPU cores naturally. Python is only orchestrating pipes and files.

## 5.18 Avoiding output-file contention

### Plain-English version

If many workers all write to the same CSV file at once, they would need synchronization. That creates overhead and failure risk.

### Technical version

Each `bin/dern` worker writes to its own `.part` file. The wrapper concatenates them afterward.

### Why this matters

It is simple, reliable, and fast.

## 5.19 Summary aggregation instead of post-processing full CSV output

### Plain-English version

If all you need is "how many graphs had dERN 0, 1, 2, or >=3," you should not reread all CSV rows after the run.

### Technical version

Each worker emits a compact summary line on stderr. The wrapper parses those summary lines and aggregates them.

### Why this matters

It avoids a second full scan over all output data.

## 6. Original Global ERN Pipeline Optimizations

The original ERN script was not "unoptimized." It already had several good optimizations. Its main problem was that the overall architecture was still harsher than the dERN pipeline.

## 6.1 Direct graph6 bit decoding

### Plain-English version

Instead of immediately converting each graph into a heavy object, the script decodes graph6 once into low-level bit information it can reuse.

### Technical version

`decode_graph6_with_edge_bits` returns:

1. `n`,
2. the graph6 header length,
3. bitset rows,
4. the exact bit positions of the present edges.

### Why this matters

That one pass supports split testing, edge counting, and edge-card generation without repeated parsing.

## 6.2 Fast split-graph recognition

### Plain-English version

The script does not try to search for a split partition by brute force. It uses a degree-sequence characterization.

### Technical version

`is_split_graph` implements the Hammer-Simeone degree criterion.

### Why this matters

It turns split recognition into a cheap arithmetic test.

## 6.3 Build deleted-edge cards by mutating graph6 bits directly

### Plain-English version

To remove one edge from a graph, the script does not rebuild the whole graph from scratch. It flips the corresponding bit in the graph6 payload.

### Technical version

`edge_deleted_card_counter` modifies a `bytearray` of the graph6 line, toggling exactly one edge bit at a time.

### Why this matters

This is dramatically cheaper than reconstructing a graph object and re-encoding it for each deleted edge.

## 6.4 Batch canonicalization with `labelg`

### Plain-English version

Calling `labelg` one graph at a time would be expensive. The script batches many graph6 strings into one call.

### Technical version

`NautyCanonicalizer` groups inputs into batches and canonicalizes them in chunks through `labelg -q`.

### Why this matters

It reduces process-launch overhead and lets `labelg` amortize its setup work.

## 6.5 Optional raw-card cache

### Plain-English version

If the same raw card graph appears many times, canonicalize it once and reuse the answer.

### Technical version

`NautyCanonicalizer` supports `use_raw_cache`, storing `raw_graph6 -> canonical_id`.

### Why this matters

This can save substantial time on repeated cards, especially at larger `n`.

### Tradeoff

This uses more RAM. Speed goes up if repetition is high enough to justify the memory.

## 6.6 Parallel `labelg` subprocesses

### Plain-English version

The script itself is Python, but canonicalization is outsourced to external binaries. That means several canonicalization batches can run at once.

### Technical version

`NautyCanonicalizer` can create a `ThreadPoolExecutor`. The threads are not doing the canonicalization themselves. They are just launching and waiting for separate `labelg` subprocesses.

### Why this matters

This captures parallelism where it actually exists: in the external canonicalization work.

## 6.7 Canonicalize only when a card buffer fills

### Plain-English version

The script does not canonicalize every graph's deck the moment it is created. It buffers many raw cards first.

### Technical version

`process_n` accumulates `pending` decks and flushes them through `finalize_pending_decks` when `pending_cards >= deck_batch_cards`.

### Why this matters

This improves batching and reduces repeated overhead.

## 6.8 Compare only graphs with the same edge count

### Plain-English version

A graph with 12 edges and a graph with 15 edges cannot have the same edge deck size. So they cannot block each other in the way ERN cares about.

### Technical version

The script groups records by `edge_count` and only compares a split graph against other graphs in the same edge-count bucket.

### Why this matters

This is a strong exact pruning rule.

## 6.9 Inverted index from card type to graphs containing that type

### Plain-English version

If a graph `H` does not contain any card type that appears in `G`'s deck, it cannot confuse `G`. So do not compare them.

### Technical version

`process_n` builds `postings` lists mapping each relevant canonical card type to the local graph indices whose decks contain that type. For a target graph, candidate blockers are only those graphs that appear in the union of the postings for its deck types.

### Why this matters

This removes a huge number of useless deck comparisons.

## 6.10 Dominance pruning on blocker vectors

### Plain-English version

Some fake competitors are obviously weaker than others. If one competitor has at least as many copies of every relevant card type as another, then the weaker one never makes the problem harder.

### Technical version

`maximal_blocker_vectors` keeps only componentwise maximal blocker vectors.

If blocker `A` dominates blocker `B`, then any subset of cards that excludes `A` automatically excludes `B`. Therefore `B` does not need to be kept.

### Why this matters

It shrinks the exact search space before the expensive combinatorial part begins.

## 6.11 Specialized fast tests for `k = 1` and `k = 2`

### Plain-English version

Before running a general-purpose search, the script checks the easiest cases with highly tuned logic.

### Technical version

`fast_ern_k1_k2` builds bit masks describing which blockers have at least 1 or at least 2 copies of each card type. It then checks:

1. whether some single card type is unique to `G`,
2. whether two copies of one card type are enough,
3. whether some pair of card types has no common blocker.

### Why this matters

Many graphs are settled at `ERN = 1` or `ERN = 2`, so avoiding general search here is crucial.

## 6.12 Exact dynamic programming for `k >= 3`

### Plain-English version

Once the easy cases fail, the script needs to answer a harder question:

"Can I pick `k` cards from `G` so that every blocker is forced to miss at least one required multiplicity?"

That is a combinatorial search problem, so the script uses dynamic programming and memoization.

### Technical version

`exists_distinguishing_subset_of_size` builds:

1. `ge_masks`, telling which blockers can satisfy at least `c` copies of a given card type,
2. `suffix` bounds for pruning,
3. a memoized DFS over `(i, remaining, mask)`.

The state means:

1. `i`: which card type we are deciding on,
2. `remaining`: how many cards still need to be chosen,
3. `mask`: which blockers are still compatible.

### Why this matters

This is exact and much faster than naive subset enumeration.

### Tradeoff

It is still expensive when blocker sets are large. This is one reason ERN remains harder than dERN.

## 7. New Local ERN Pipeline Optimizations Added In This Session

The biggest change we made was not a micro-optimization. It was replacing the outer architecture for the scalable path.

## 7.1 Switching the default ERN strategy from "global" to "local"

### Plain-English version

The old ERN method tried to understand the whole world of graphs at once. The new method studies one split graph at a time and asks:

"Which other graphs could possibly pretend to be this one?"

That localizes the work.

### Technical version

`split_edge_reconstruction.py` now has:

1. `--mode global`: the original exhaustive all-graph method.
2. `--mode local`: the newer exact target-by-target method.

The local solver is implemented by `LocalErnSolver` and `process_n_local`.

### Why this matters

For large `n`, the global universe of all unlabeled graphs becomes the bottleneck. The local method avoids paying that cost.

## 7.2 Generating only split targets for ERN as well

### Plain-English version

We reused the best front-end idea from the dERN pipeline: do not generate graphs you never wanted.

### Technical version

`iter_target_graphs` now supports `target_source = split`, using `dern/gen_split_n.sh` as the source of targets.

This means the local ERN solver can operate directly on deduplicated split graphs instead of filtering them out of all unlabeled graphs.

### Why this matters

This is the single largest architectural win for large-`n` ERN runs.

## 7.3 Exact blocker reduction by "one-edge extensions of the target's cards"

### Plain-English version

Here is the key idea behind the new local ERN solver:

If another graph `H` can confuse the target graph `G`, then `H` must share at least one edge-deleted card with `G`.

If `H` shares a card `C` with `G`, then `H` can be formed by taking that card `C` and adding one edge back.

So instead of considering every graph on `n` vertices, the local solver only considers graphs obtainable by adding one edge to one of `G`'s cards.

### Technical version

For each canonical card type in `G`'s deck, `_parent_candidates`:

1. enumerates every one-edge extension of that card,
2. canonicalizes those extensions,
3. deduplicates them.

The candidate blocker universe is the union of those parent sets, minus `G` itself.

This is exact, not heuristic. Every blocker must belong to that union.

### Why this matters

This shrinks the search universe from "all unlabeled graphs of order `n`" to "all possible parents of the cards that actually occur in `G`."

That is a drastic reduction.

## 7.4 Building one-edge extensions by graph6 bit mutation

### Plain-English version

Just as the global solver removed edges by flipping graph6 bits, the local solver adds edges by flipping graph6 bits.

### Technical version

`single_edge_extensions` walks every absent edge bit position and toggles it in a `bytearray` copy of the graph6 string.

### Why this matters

This keeps the local solver's parent generation cheap and allocation-light.

## 7.5 Reusing canonical parent sets across targets

### Plain-English version

If the same deleted card type appears in many target graphs, its possible parents are also the same. There is no reason to recompute them.

### Technical version

`LocalErnSolver` stores `parent_cache: card_canon -> tuple(parent_canons)`.

### Why this matters

Repeated card types are common. This cache turns repeated reconstruction work into a lookup.

## 7.6 Separate batch canonicalizer for the local solver

### Plain-English version

The local solver does not need global integer IDs for card types. It mostly needs canonical graph6 strings. So we created a simpler canonicalization helper tailored to that use case.

### Technical version

`Graph6BatchCanonicalizer` stores `raw_graph6 -> canonical_graph6`.

### Why this matters

This keeps the local path simpler and avoids carrying around extra global indexing machinery it does not need.

## 7.7 Skipping canonicalization of target graphs themselves

### Plain-English version

If the target graphs already come from `shortg` or `geng`, they are already canonical representatives. Running `labelg` again on the target itself is pointless.

### Technical version

`LocalErnSolver.solve` now sets `gcanon = g6` directly.

### Why this matters

This removes one unnecessary `labelg` call per target graph.

### Safety

This is safe for the current target sources because both `geng` and `shortg` emit canonical representatives.

## 7.8 Very early `ERN = 1` detection in local mode

### Plain-English version

If no competing parent graph exists for the target's cards, or if one card's parent set is just the target itself, then one card is enough.

### Technical version

The local solver returns `ERN = 1` when:

1. the union of candidate parents becomes empty after removing `G`, or
2. some card's parent set is exactly `{G}`.

### Why this matters

This resolves easy targets before any blocker deck construction.

## 7.9 Very early `ERN = 2` detection by parent-set intersections

### Plain-English version

If two cards only have one common parent, and that parent is `G`, then those two cards already reconstruct the graph.

### Technical version

The local solver sorts card types by parent-set size and checks pairwise intersections first among the most informative cards.

### Why this matters

It avoids building blocker decks for many graphs whose answer is already `2`.

## 7.10 Only build blocker decks for graphs that survived the candidate test

### Plain-English version

The expensive part of ERN is not "look at the target's cards." It is "look at the cards of all the fake competitors too." So the local solver delays that expensive part until it has a much smaller exact blocker set.

### Technical version

Only after the candidate union is fixed does the solver:

1. generate raw edge-deleted decks for candidate blockers,
2. canonicalize those blocker cards in batch,
3. project each blocker deck onto the target's relevant card types.

### Why this matters

This is where the local method wins most of its time relative to naive target-by-target checking.

## 7.11 Project blocker decks onto relevant card types only

### Plain-English version

If the target graph never uses a certain card type, that card type cannot help distinguish the target. So do not keep it.

### Technical version

After canonicalizing blocker cards, the local solver only keeps multiplicities for card types that appear in the target's deck.

### Why this matters

This reduces vector dimension before the exact ERN computation.

## 7.12 Reusing the exact ERN combinatorial core

### Plain-English version

We did not replace the mathematical decision logic for ERN. We changed how the blocker set is obtained, then fed that smaller exact blocker set into the same exact decision engine.

### Technical version

The local solver still calls `edge_reconstruction_number`, which still uses:

1. dominance pruning,
2. specialized `k = 1, 2` logic,
3. exact DP for larger `k`.

### Why this matters

This keeps correctness high. The local pipeline changes the outer search space, not the meaning of ERN.

## 7.13 Per-worker persistent caches in local parallel mode

### Plain-English version

If each worker handled one target and then died, all its cached knowledge would be lost. Instead, workers stay alive and solve many chunks.

### Technical version

`ProcessPoolExecutor` is created with `initializer=init_local_solver_worker`. Each worker process creates one `LocalErnSolver` and reuses its caches across tasks.

### Why this matters

This is especially helpful for:

1. repeated card types,
2. repeated parent sets,
3. repeated canonicalizations.

## 7.14 Chunked work submission

### Plain-English version

Sending one graph per task causes too much scheduling overhead. Sending every graph at once causes memory blow-up. The script sends moderate-sized chunks.

### Technical version

`chunked` groups targets, and `process_n_local` submits those chunks to the process pool. It also limits the number of outstanding tasks with:

`max_pending = max(2, jobs * 2)`

### Why this matters

This balances:

1. scheduling overhead,
2. load balancing,
3. memory usage.

## 7.15 Backpressure on the task queue

### Plain-English version

If the main process floods the worker queue with too many chunks, memory usage rises and responsiveness drops.

### Technical version

`process_n_local` only keeps a bounded number of futures outstanding and uses `wait(..., return_when=FIRST_COMPLETED)`.

### Why this matters

This keeps the pipeline controlled instead of "firehose everything into the executor."

## 7.16 Exact split-target input handling with stderr filtering

### Plain-English version

`shortg` writes informational lines like `>A shortg` and `>Z ...` to stderr. Those are not graphs. The new ERN reader captures stderr separately so those lines do not pollute the graph stream.

### Technical version

`iter_split_graphs` launches the generator with separate stdout and stderr, filters any accidental `>` lines, and only reports stderr if the generator actually fails.

### Why this matters

This makes the split-target path reliable under WSL and shell wrappers.

## 7.17 Operational fallback for WSL nauty binary names

### Plain-English version

On Ubuntu and Debian, the nauty tools are often named `nauty-geng`, `nauty-labelg`, and so on, not just `geng` and `labelg`.

### Technical version

`resolve_tool_command` now tries distro-specific fallback names.

### Why this matters

This removes a common setup failure on WSL.

## 7.18 Repairing the moved `dern/` shell scripts

### Plain-English version

The scripts used to assume they lived in a different directory structure. After being moved into `dern/`, their relative paths were wrong.

### Technical version

The scripts now compute `SCRIPT_DIR` and reference binaries and sibling scripts relative to that directory.

### Why this matters

Without this fix, the optimized generator pipeline would not have been usable from the current repo layout.

## 7.19 Adding a wrapper for the fast ERN path

### Plain-English version

Complex flag combinations invite mistakes. A wrapper makes the fast path easy to run correctly.

### Technical version

`dern/run_split_ern_n_parallel.sh` calls:

`split_edge_reconstruction.py --mode local --target-source split`

with the right generator script and worker count.

### Why this matters

Better ergonomics makes the optimization actually usable.

## 8. What Stayed The Same Mathematically

It is important to separate:

1. mathematical shortcuts that are exact, and
2. implementation shortcuts that are only engineering choices.

The following are exact:

1. Split-graph generation from bipartite graphs plus clique completion.
2. `shortg` deduplication.
3. Canonicalization with nauty.
4. dERN candidate generation using the degree-sum constraint.
5. ERN blocker restriction to one-edge extensions of target cards in local mode.
6. Dominance pruning of blocker vectors.
7. Fast `k = 1, 2` tests.
8. DP search for `k >= 3`.

The following are engineering choices that change speed but not answers:

1. chunk sizes,
2. number of jobs,
3. cache usage,
4. batch sizes,
5. part-file merging,
6. whether summaries are printed.

## 9. What We Observed In Practice

The exact numbers depend on machine, WSL setup, CPU, disk, and RAM. Still, the current code showed a clear pattern:

1. For small `n`, `global` ERN can still be faster because it shares canonicalization work across all graphs in one order.
2. For larger `n`, `global` ERN becomes impractical because generating all unlabeled graphs is the real bottleneck.
3. The new `local` split-target ERN path is slower on tiny orders but scales much better once the graph universe explodes.

Observed during this session:

1. `local` mode matched `global` mode exactly through `n = 6`.
2. `n = 10` in local split mode completed in roughly 42 seconds on this machine with `--jobs 4 --raw-card-cache`.
3. The old all-graph `global` method was still faster at `n = 8`, which is consistent with the idea that the crossover only appears when all-graph enumeration starts dominating.

This is the expected behavior.

## 10. Why ERN Is Still Harder Than dERN Even After The New Optimizations

The new local ERN path is better, but ERN remains fundamentally harder.

### Reason 1: weaker cards

An ERN card is only the deleted graph. A dERN card is the deleted graph plus a degree-associated label.

That means dERN candidate sets are smaller.

### Reason 2: bigger blocker families

Because ERN cards are weaker, more non-isomorphic graphs can mimic parts of the deck.

That means the blocker universe is larger.

### Reason 3: expensive exact search for larger `k`

When `ERN` is not settled at `1` or `2`, the exact DP still has real combinatorial work to do.

### Reason 4: less cross-target sharing in local mode

The local ERN solver intentionally solves each target mostly independently. That is what lets it avoid all-graph enumeration, but it also means it shares less work across unrelated targets than the global method.

So the tradeoff is:

1. less unnecessary global work,
2. more repeated local work.

The local path wins only after the global universe gets too large.

## 11. Tuning Guide

## 11.1 For dERN

Recommended exact split-graph parallel run:

```bash
/mnt/c/Users/Tolga/Documents/ern_research/dern/run_split_dern_n_parallel.sh 14 3 results.csv 8
```

What to tune:

1. `jobs`
   Higher uses more CPU cores.

2. `NO_SHORTG=1`
   Faster front-end generation, but no exact unlabeled deduplication.

3. `SHORTG_OPTS`
   Useful if `shortg` temporary space or memory behavior needs tuning.

## 11.2 For ERN

Recommended scalable exact path:

```bash
python3 /mnt/c/Users/Tolga/Documents/ern_research/split_edge_reconstruction.py 14 --mode local --target-source split --jobs 8 --raw-card-cache
```

Or:

```bash
/mnt/c/Users/Tolga/Documents/ern_research/dern/run_split_ern_n_parallel.sh 14 8
```

What to tune:

1. `--jobs`
   More workers means more parallel target solving. Too many can increase overhead or memory.

2. `--chunk-size`
   Larger chunks reduce scheduling overhead. Smaller chunks improve load balancing.

3. `--batch-size`
   Controls `labelg` batch sizes. Too small wastes process overhead. Too large can increase latency or memory pressure.

4. `--raw-card-cache`
   Usually worth trying at larger `n` if RAM is available.

5. `--target-source split`
   This is the important scalable choice. `all` is mainly for cross-checking or experimentation.

6. `--mode global`
   Still useful for small orders and correctness validation.

## 12. What We Fixed Operationally In This Repository

These are not mathematical optimizations, but they matter because broken tooling prevents optimized code from being used.

### 12.1 Binary name fallback for WSL

The ERN script now accepts Debian-style nauty names such as `nauty-geng` and `nauty-labelg`.

### 12.2 Script relocation fixes

The `dern` shell scripts now resolve paths relative to their own directory.

### 12.3 Built helper binaries

The following binaries were built under `dern/bin`:

1. `bip2split`
2. `dern`

### 12.4 New ERN wrapper script

`dern/run_split_ern_n_parallel.sh` was added so the optimized local ERN path is easy to invoke.

## 13. What This Means Conceptually

The progression of the codebase is:

1. Start with a correct but globally exhaustive ERN approach.
2. Notice that dERN is fast partly because it only generates split graphs and uses a compiled worker.
3. Reuse the good architectural ideas from dERN where they remain mathematically valid for ERN.
4. Keep the exact ERN decision logic, but feed it a much smaller exact blocker universe.

That is the central idea of the new optimization work.

We did not "make ERN easy."

We changed the question from:

"Compare this graph against the entire order-`n` graph universe."

to:

"Compare this graph only against graphs that could possibly arise as one-edge parents of its own cards."

That is the reason the new ERN path is the right direction for larger orders.

## 14. Glossary

`graph6`

A compact text encoding for small graphs.

`canonical form`

A standard representative for an isomorphism class. Two graphs are isomorphic exactly when their canonical forms match.

`card`

A graph obtained by deleting one edge from a parent graph.

`deck`

The multiset of all such cards.

`blocker`

A competing graph that can realize the same chosen cards as the target graph and therefore prevents reconstruction from that chosen set.

`split graph`

A graph whose vertices can be partitioned into a clique and an independent set.

`dERN`

Degree-associated edge reconstruction number.

`ERN`

Edge reconstruction number.

`shortg`

A nauty/gtools utility that canonicalizes and deduplicates graphs up to isomorphism.

`labelg`

A nauty/gtools utility used here for canonical labeling.

`geng`

A nauty/gtools generator for unlabeled graphs.

`genbg`

A nauty/gtools generator for bipartite graphs.

## 15. Short Bottom Line

If you remember only five things, remember these:

1. The old dERN pipeline was fast because it solved the easier problem, generated only split graphs, and used compiled C++ workers.
2. The original ERN pipeline already had solid inner-loop optimizations, but its outer architecture still paid for all unlabeled graphs.
3. The new local ERN mode fixes that by generating split targets directly and restricting blockers to exact one-edge parents of the target's own cards.
4. The local ERN path is exact, not heuristic.
5. ERN is still fundamentally harder than dERN, so good optimization narrows the gap but does not erase it.

## 16. Where To Read In The Code

If you want to connect this write-up to the implementation quickly, use this map.

### dERN code map

`dern/small_graph.hpp`

1. `Graph16`
2. `GraphKey`
3. `GraphKeyHash`
4. `EdgeCount`
5. `Degrees`
6. `KeyFromGraph`
7. `ParseGraph6Line`
8. `ToGraph6`

`dern/dern.cpp`

1. `Canonicalize`
2. `CardKey`
3. `GenerateCandidateGraphs`
4. `IntersectionIsExactlyG`
5. `CardMultiplicityInGraph`
6. `main`

`dern/gen_split_n.sh`

1. generator selection for `genbg`
2. generator selection for `shortg`
3. split construction loop over `k = 1..n-1`

`dern/dern_parallel.py`

1. worker launch
2. round-robin fan-out
3. stderr summary parsing
4. part-file merge

### ERN code map

`split_edge_reconstruction.py`

Global path:

1. `decode_graph6_with_edge_bits`
2. `is_split_graph`
3. `edge_deleted_card_counter`
4. `NautyCanonicalizer`
5. `finalize_pending_decks`
6. `maximal_blocker_vectors`
7. `fast_ern_k1_k2`
8. `exists_distinguishing_subset_of_size`
9. `edge_reconstruction_number`
10. `process_n`

Local path:

1. `Graph6BatchCanonicalizer`
2. `single_edge_extensions`
3. `LocalErnSolver`
4. `iter_split_graphs`
5. `iter_target_graphs`
6. `process_n_local`
7. CLI flags `--mode`, `--target-source`, `--jobs`, `--chunk-size`, `--raw-card-cache`

Wrappers:

1. `dern/run_split_ern_n_parallel.sh`
2. `dern/run_split_dern_n.sh`
3. `dern/run_split_dern_n_parallel.sh`
