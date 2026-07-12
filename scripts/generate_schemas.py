import json

from scan64.lessonspec.models import DomainEventEnvelope, LessonSpec


def main():
    with open("schemas/lesson-spec.schema.json", "w") as f:
        json.dump(LessonSpec.model_json_schema(), f, indent=2)
    with open("schemas/events.schema.json", "w") as f:
        json.dump(DomainEventEnvelope.model_json_schema(), f, indent=2)

if __name__ == "__main__":
    main()
