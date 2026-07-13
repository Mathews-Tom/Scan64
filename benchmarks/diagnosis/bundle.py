import json
from pathlib import Path
from typing import Any


def export_reproducibility_bundle(
    output_dir: Path,
    bundle_id: str,
    pgn_content: str,
    fen: str,
    engine_config: dict[str, Any],
    analysis_response: dict[str, Any],
    detector_versions: dict[str, str],
    diagnosis_result: dict[str, Any],
    profile_snapshot: dict[str, Any],
    lesson_spec: dict[str, Any],
    verification_report: dict[str, Any],
) -> Path:
    """
    Export a reproducibility bundle for a generated lesson (§26.2).
    """
    bundle_dir = output_dir / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    with open(bundle_dir / "source.pgn", "w") as f:
        f.write(pgn_content)

    manifest = {
        "fen": fen,
        "engine_config": engine_config,
        "analysis_response": analysis_response,
        "detector_versions": detector_versions,
        "diagnosis_result": diagnosis_result,
        "profile_snapshot": profile_snapshot,
        "lesson_spec": lesson_spec,
        "verification_report": verification_report,
    }

    with open(bundle_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    return bundle_dir
