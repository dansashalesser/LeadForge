# Skill Extraction Plan: {{REPO_NAME}}

**Generated**: {{TIMESTAMP}}
**Source**: {{REPO_URL_OR_PATH}}

---

## Repository Summary

- **Type**: {{REPO_TYPE}} (library / framework / CLI tool / web app / data pipeline / ML project / agent system)
- **Primary Language**: {{LANGUAGE}}
- **Key Dependencies**: {{KEY_DEPS}}
- **Structure**: {{STRUCTURE_SUMMARY}}
- **Documentation Quality**: {{DOC_QUALITY}} (none / minimal / moderate / comprehensive)

## Candidates

{{CANDIDATE_COUNT}} candidates identified (threshold: score >= 6/12).

| Rank | Name | Artifact Type | Source Module | Score | Risk | Rationale |
|------|------|--------------|--------------|-------|------|-----------|
| {{RANK}} | {{NAME}} | {{skill/hook/script/command/routine}} | {{SOURCE_PATH}} | {{SCORE}}/12 | {{RISK}} | {{ONE_LINE_RATIONALE}} |

### Candidate Details

#### {{RANK}}. {{NAME}} (Score: {{SCORE}}/12) — `{{artifact-type}}`

- **Source**: `{{SOURCE_PATH}}`
- **Artifact type**: `{{skill / hook / script / command / routine}}`
- **Output path**: `{{destination path}}`
- **Scoring Breakdown**:
  - Recurrence: {{0-3}} — {{reason}}
  - Code Quality: {{0-3}} — {{reason}}
  - Domain Expertise: {{0-3}} — {{reason}}
  - Generalizability: {{0-3}} — {{reason}}
  - Modifiers: {{list applied modifiers}}
- **What it teaches / does**: {{DESCRIPTION}}
- **Risk level**: {{safe / unknown / caution}}
- **Existing overlap**: {{none / partial with NAME}}
- **Recommended action**: {{extract / skip / merge with existing}}

_Repeat for each candidate._

## Relationship Map

Connections between extracted candidates and existing skills:

- {{SKILL_A}} → requires → {{SKILL_B}}
- {{SKILL_A}} → extends → {{EXISTING_SKILL}}
- {{SKILL_A}} → subset-of → {{EXISTING_SKILL}}

## Recommendations

- {{Key recommendations about which candidates to prioritize}}
- {{Any candidates that should be merged into a single skill}}
- {{Suggestions for skill naming to fit the existing ecosystem}}

## Next Step

Review this plan. For each candidate:
- Edit the **Artifact type** if the classification feels wrong
- Remove candidates you don't want
- Split a candidate into two rows if it warrants multiple artifact types

Then run:
```
/kiro:skill-extract <path-to-this-plan>
```
