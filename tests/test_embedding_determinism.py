"""Cross-process regression test for embedding determinism.

A document's tag block must be assembled in authored order, never from an
unordered collection. ``set`` iteration order depends on the process's
string-hash secret, so a set-based implementation makes the embedding — and
therefore the search ranking — differ between two processes that read the
identical file. That defect is invisible to any single-process test: within one
process the seed is constant, so the order looks stable. It only appears when
the same file is prepared in processes started with different seeds.

This test therefore crosses a real process boundary. It spawns three children,
one per seed, with ``PYTHONHASHSEED`` overridden in each child's environment —
the override is what makes the seed differ, and each child reports a
fingerprint of its own effective seed so the test fails loudly if the override
never took effect instead of passing vacuously.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# The probe child, kept as source text and written to tmp_path at test time —
# the same shape as tests/test_server.py's _STUB_SENTENCE_TRANSFORMERS.
#
# It builds the indexer with VaultIndexer.__new__ so __init__ (which loads the
# embedding model the container does not have) never runs;
# _prepare_text_for_embedding needs no instance state. It prints exactly one
# JSON line on stdout and exits non-zero on any failure, so a broken probe is a
# loud failure rather than an empty stdout that a lenient parse could read as
# agreement.
_DETERMINISM_PROBE = '''\
"""Report a fingerprint of this process's embedding inputs for one file pair."""

import hashlib
import json
import os
import sys
from pathlib import Path

from semantic_search.indexer import VaultIndexer


def main() -> int:
    """Prepare both fixtures and print one JSON line describing the result."""
    tagged_path = Path(sys.argv[1])
    untagged_path = Path(sys.argv[2])
    indexer = VaultIndexer.__new__(VaultIndexer)
    tagged_prepared = indexer._prepare_text_for_embedding(
        tagged_path, tagged_path.read_text()
    )
    untagged_prepared = indexer._prepare_text_for_embedding(
        untagged_path, untagged_path.read_text()
    )
    print(
        json.dumps(
            {
                "pid": os.getpid(),
                "hash_fingerprint": str(hash("semantic-search-hash-probe")),
                "tagged_sha256": hashlib.sha256(tagged_prepared.encode()).hexdigest(),
                "tagged_tag_line": tagged_prepared.split("\\n")[3],
                "untagged_sha256": hashlib.sha256(untagged_prepared.encode()).hexdigest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

TAGGED_FIXTURE = "---\ntags: [alpha, beta, gamma, delta, epsilon]\n---\n\n# Fixture\n\nbody text\n"
UNTAGGED_FIXTURE = "---\n---\n\n# Fixture\n\nbody text\n"

# Three distinct non-zero seeds. Three children is the whole cost of this test:
# each child imports sentence_transformers, which takes seconds.
SEEDS = ("1", "2", "3")

TAG_LINE = "alpha beta gamma delta epsilon"


def _write_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    """Write both fixtures, each in its own directory, both named fixture.md.

    The filename stem is embedded three times in the prepared text, so the two
    fixtures must share a name for the digest to differ only because of the tag
    block. A different stem would move the digest for an unrelated reason.
    """
    tagged_dir = tmp_path / "tagged"
    untagged_dir = tmp_path / "untagged"
    tagged_dir.mkdir()
    untagged_dir.mkdir()
    tagged_path = tagged_dir / "fixture.md"
    untagged_path = untagged_dir / "fixture.md"
    tagged_path.write_text(TAGGED_FIXTURE)
    untagged_path.write_text(UNTAGGED_FIXTURE)
    return tagged_path, untagged_path


def _run_probe(
    probe_path: Path, tagged_path: Path, untagged_path: Path, seed: str
) -> dict[str, Any]:
    """Run the probe child under ``seed`` and return its parsed JSON line.

    PYTHONHASHSEED is set only in the child's environment, never in the parent's
    or the service's: the override exists so the three children get three
    different string-hash secrets.
    """
    env = {**os.environ, "PYTHONHASHSEED": seed}
    result = subprocess.run(
        [sys.executable, str(probe_path), str(tagged_path), str(untagged_path)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"probe child under PYTHONHASHSEED={seed} exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(
            f"probe child under PYTHONHASHSEED={seed} printed no stdout\nstderr:\n{result.stderr}"
        )
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"probe child under PYTHONHASHSEED={seed} printed no JSON on its last "
            f"line ({error})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from error
    if not isinstance(payload, dict):
        raise AssertionError(
            f"probe child under PYTHONHASHSEED={seed} printed a non-object JSON "
            f"line\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    payload["seed"] = seed
    return payload


class TestEmbeddingDeterminismAcrossProcesses:
    """The same unchanged file yields the same embedding input in every process."""

    def test_tag_block_is_seed_independent_across_processes(self, tmp_path: Path) -> None:
        """Three processes, three seeds: one tagged digest, one untagged digest.

        The untagged fixture is the control: untagged files were already
        deterministic, so a failure on the tagged digest while the untagged one
        holds points at the tag block rather than at everything.
        """
        probe_path = tmp_path / "probe.py"
        probe_path.write_text(_DETERMINISM_PROBE)
        tagged_path, untagged_path = _write_fixtures(tmp_path)

        runs = [_run_probe(probe_path, tagged_path, untagged_path, seed) for seed in SEEDS]

        # A leading newline keeps the first run line off pytest's progress line,
        # so a `-s` run shows one self-contained line per seed.
        print()
        for run in runs:
            print(
                f"PYTHONHASHSEED={run['seed']} pid={run['pid']} "
                f"fingerprint={run['hash_fingerprint']} "
                f"tagged_sha256={run['tagged_sha256']}"
            )

        pids = [run["pid"] for run in runs]
        assert len(set(pids)) == len(SEEDS), (
            f"expected {len(SEEDS)} distinct child pids, got {pids} — the runs are "
            "not separate processes, so this test cannot see the cross-process defect"
        )

        fingerprints = [run["hash_fingerprint"] for run in runs]
        assert len(set(fingerprints)) == len(SEEDS), (
            f"expected {len(SEEDS)} distinct hash fingerprints, got {fingerprints} — "
            "the PYTHONHASHSEED override did not take effect, so the determinism "
            "assertion below would pass vacuously"
        )

        tagged_digests = {run["tagged_sha256"] for run in runs}
        assert len(tagged_digests) == 1, (
            f"the same tagged file produced {len(tagged_digests)} distinct digests "
            f"across seeds {list(SEEDS)}: {sorted(tagged_digests)} — the embedding "
            "input depends on the process's string-hash seed"
        )

        for run in runs:
            assert run["tagged_tag_line"] == TAG_LINE, (
                f"seed {run['seed']} produced tag line {run['tagged_tag_line']!r}, "
                f"expected authored order {TAG_LINE!r}"
            )

        untagged_digests = {run["untagged_sha256"] for run in runs}
        assert len(untagged_digests) == 1, (
            f"the control (untagged) file produced {len(untagged_digests)} distinct "
            f"digests across seeds {list(SEEDS)}: {sorted(untagged_digests)} — this "
            "failure is not about the tag block"
        )
