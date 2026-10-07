# Test Backlinks — Spec Traceability in Test Files

Every test file that verifies a spec requirement MUST include a backlink comment pointing to the requirement it covers.

## Format

Use the language-appropriate comment syntax:

```python
# Verifies: specs/{feature}/requirements.md#1.1
```

```typescript
// Verifies: specs/{feature}/requirements.md#2.3
```

```java
// Verifies: specs/{feature}/requirements.md#3.1
```

## Rules

- One backlink per test function/block — place it immediately above the test
- The `#N.M` suffix references the EARS requirement ID from requirements.md
- A test covering multiple requirements lists them comma-separated: `#1.1, #1.2`
- Backlinks are checked by `validate-impl` — missing backlinks are flagged as warnings

## Example

```python
# Verifies: specs/user-auth/requirements.md#1.1
def test_login_with_valid_credentials():
    result = auth_service.login("user@example.com", "valid_pass")
    assert result.success is True

# Verifies: specs/user-auth/requirements.md#1.3
def test_login_rate_limiting():
    for _ in range(6):
        auth_service.login("user@example.com", "wrong_pass")
    result = auth_service.login("user@example.com", "valid_pass")
    assert result.locked is True
```

## Why

Backlinks make traceability deterministic and auditable. Instead of inferring which test covers which requirement via grep matching, the relationship is explicit in code.
