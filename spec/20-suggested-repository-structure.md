## §20. Suggested Repository Structure

```text
exp2res/
  pyproject.toml
  README.md

  docs/
    SDD.md
    SELF_ASSESSMENT_MODEL.md
    RESUME_EXPORT_MODEL.md
    VERIFICATION_RULES.md
    INTEGRATION_CONTRACTS.md
    adr/
      0001-self-assessment-first.md
      0002-append-only-evidence.md
      0003-resume-as-export.md
      0004-no-automatic-semantic-promotion.md

  exp2res/
    __init__.py
    cli.py

    domain/
      models.py
      temporal.py
      ownership.py
      evidence.py
      self_assessment.py
      validation.py

    storage/
      sqlite.py
      migrations.py
      repositories.py

    pipeline/
      capture_raw.py
      normalize_evidence.py
      extract_facts.py
      generate_gaps.py
      detect_contradictions.py
      generate_signals.py
      generate_assessment.py
      verify_assessment.py
      parse_jd.py
      match_jd.py
      generate_resume.py
      verify_resume.py
      export.py

    integrations/
      tick_like.py
      atlas.py
      github.py
      local_files.py

    llm/
      client.py
      prompts.py
      schemas.py

    services/
      raw_log_service.py
      evidence_service.py
      fact_service.py
      assessment_service.py
      resume_service.py

    exports/
      self_assessment_markdown.py
      resume_markdown.py
      evidence_map.py
      verification_report.py
      gap_questions.py
      contradictions.py

  tests/
    test_append_only_logs.py
    test_temporal_precision.py
    test_ownership_levels.py
    test_fact_sources_required.py
    test_self_claim_sources_required.py
    test_no_flattery.py
    test_no_diagnostic_claims.py
    test_no_resume_without_evidence.py
    test_no_employment_framing.py
    test_tick_like_import_is_weak_evidence.py
    test_atlas_trail_not_skill_claim.py
    test_contradictions_preserved.py

  examples/
    logs/
      exp2res_daily.md
      storyworm_retro.md
      bitgn_competition.md

    imports/
      tick_like_export.jsonl
      atlas_artifacts.json

    jobs/
      agent_engineer.md

    outputs/
      self_assessment.md
      resume.md
      evidence_map.json
      verification_report.md
```

---

