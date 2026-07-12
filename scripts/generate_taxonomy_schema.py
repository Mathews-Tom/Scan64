import json
from pathlib import Path

from scan64.learning.diagnosis.taxonomy.models import SkillDefinition


def main() -> None:
    schema = SkillDefinition.model_json_schema()
    out_path = Path("schemas/taxonomy.schema.json")
    out_path.parent.mkdir(exist_ok=True, parents=True)
    out_path.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Wrote schema to {out_path}")


if __name__ == "__main__":
    main()
