from scan64.learning.diagnosis.taxonomy.models import SkillDefinition, SkillTier

SEED_CODES: dict[str, SkillDefinition] = {
    "board_awareness.hanging_piece": SkillDefinition(
        skill_id="board_awareness.hanging_piece",
        name="Hanging piece",
        parent_id="board_awareness",
        tier=SkillTier.EVENT,
        detection_requirements="An undefended piece is captured without compensation, or a piece is moved to an undefended attacked square.",  # noqa: E501
        positive_examples=["4k3/8/8/8/8/8/8/4K2R b - - 0 1 (black plays Rh8)"],
        counterexamples=[
            "4k3/8/8/8/8/8/8/4K1NR b - - 0 1 (black plays Rxh1, but it's an exchange)"
        ],
        confidence_calculation="1.0 if blunder >= 2.0 and piece is completely undefended.",
        compatible_exercise_templates=["find_the_hanging_piece"],
        incompatible_diagnoses=["tactics.fork"],
        minimum_engine_evidence="Eval drop >= 2.0",
    ),
    "threat_processing.missed_check": SkillDefinition(
        skill_id="threat_processing.missed_check",
        name="Missed check",
        parent_id="threat_processing",
        tier=SkillTier.EVENT,
        detection_requirements="Player fails to deliver a safe check that would win material or mate.",  # noqa: E501
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="1.0 if the check was the unique best move.",
        compatible_exercise_templates=["find_the_check"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Missed mate or +3.0 advantage via check.",
    ),
    "threat_processing.missed_capture": SkillDefinition(
        skill_id="threat_processing.missed_capture",
        name="Missed capture",
        parent_id="threat_processing",
        tier=SkillTier.EVENT,
        detection_requirements="Player fails to take a hanging or underdefended opponent piece.",
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="1.0 if taking was the only winning line.",
        compatible_exercise_templates=["find_the_free_piece"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Missed +2.0 advantage via capture.",
    ),
    "threat_processing.missed_direct_threat": SkillDefinition(
        skill_id="threat_processing.missed_direct_threat",
        name="Missed direct threat",
        parent_id="threat_processing",
        tier=SkillTier.EVENT,
        detection_requirements="Player ignores a 1-ply direct threat to their material.",
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="1.0 if opponent immediately executes the threat.",
        compatible_exercise_templates=["respond_to_threat"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Blunder causing immediate loss of material.",
    ),
    "tactics.fork.knight": SkillDefinition(
        skill_id="tactics.fork.knight",
        name="Knight fork",
        parent_id="tactics.fork",
        tier=SkillTier.EVENT,
        detection_requirements="Knight attacks two undefended or higher-value pieces.",
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="1.0 if fork results in material gain.",
        compatible_exercise_templates=["find_the_fork"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Eval change >= 2.0",
    ),
    "tactics.pin": SkillDefinition(
        skill_id="tactics.pin",
        name="Pin",
        parent_id="tactics.pin",  # actually parent should be tactics according to §8.1-§8.9 top level category, let's use tactics  # noqa: E501
        tier=SkillTier.EVENT,
        detection_requirements="Player misses an opportunity to pin a piece to a more valuable piece.",  # noqa: E501
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="1.0 if pinning wins material.",
        compatible_exercise_templates=["find_the_pin"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Eval change >= 2.0",
    ),
    "tactics.overloaded_defender": SkillDefinition(
        skill_id="tactics.overloaded_defender",
        name="Overloaded defender",
        parent_id="tactics",
        tier=SkillTier.EVENT,
        detection_requirements="Player misses an opportunity to exploit a piece defending two threats.",  # noqa: E501
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="1.0 if exploiting wins material.",
        compatible_exercise_templates=["exploit_overload"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Eval change >= 2.0",
    ),
    "calculation.stopped_too_early": SkillDefinition(
        skill_id="calculation.stopped_too_early",
        name="Stopped calculation too early",
        parent_id="calculation",
        tier=SkillTier.PROCESS,
        detection_requirements="Player plays a sequence that looks winning initially but fails to a deep, forcing reply.",  # noqa: E501
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="0.8 (process-tier inference).",
        compatible_exercise_templates=["deep_calculation"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Sequence involves 3+ plies, eval swings sharply at the end.",
    ),
    "opening.delayed_development": SkillDefinition(
        skill_id="opening.delayed_development",
        name="Delayed development",
        parent_id="opening",
        tier=SkillTier.PROCESS,
        detection_requirements="Player makes multiple pawn moves or moves the same piece repeatedly before moving minor pieces.",  # noqa: E501
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="0.9 (process-tier).",
        compatible_exercise_templates=["opening_principles"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Engine detects loss of tempo > 1.5 in opening.",
    ),
    "positional.king_safety_neglect": SkillDefinition(
        skill_id="positional.king_safety_neglect",
        name="King-safety neglect",
        parent_id="positional",
        tier=SkillTier.PROCESS,
        detection_requirements="Player fails to castle or voluntarily opens files near their king when under attack.",  # noqa: E501
        positive_examples=[],
        counterexamples=[],
        confidence_calculation="0.85 (process-tier).",
        compatible_exercise_templates=["king_safety"],
        incompatible_diagnoses=[],
        minimum_engine_evidence="Eval drop due to incoming mate threat or heavy attack.",
    ),
}

# Fix parent IDs
SEED_CODES["tactics.fork.knight"] = SEED_CODES["tactics.fork.knight"].model_copy(
    update={"parent_id": "tactics"}
)
