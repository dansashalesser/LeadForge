# Property-Based Testing — Beyond Example Tests

Property-based testing complements example-based TDD by defining invariants that must hold for all inputs. Agents can generate hundreds of test cases in minutes, catching edge cases that hand-picked examples miss.

## When to Use Property-Based Tests

Evaluate after writing example-based tests in the RED phase. Use property tests when the function under test exhibits any of these characteristics:

| Property Type | Description | Example |
|---------------|-------------|---------|
| **Roundtrip** | Encoding then decoding returns original input | `decode(encode(x)) == x` |
| **Invariant** | Output always satisfies a constraint | Sorted output, non-negative values, length preservation |
| **Idempotent** | Applying twice equals applying once | `format(format(x)) == format(x)` |
| **Commutative** | Order doesn't matter | `merge(a, b) == merge(b, a)` |
| **Oracle** | A slow-but-correct reference exists | Optimized sort vs naive sort produce same result |
| **State machine** | Only valid transitions occur | No state reaches an invalid successor |

## When NOT to Use Property Tests

- UI rendering logic (use visual/screenshot tests instead)
- Integration tests with external services
- Simple CRUD with no transformation logic
- When the property would just restate the implementation

## Recommended Libraries

| Ecosystem | Library | Install |
|-----------|---------|---------|
| Python | `hypothesis` | `pip install hypothesis` |
| JS/TS | `fast-check` | `npm install fast-check` |
| Rust | `proptest` | `cargo add proptest --dev` |
| Go | `testing/quick` (stdlib) or `gopter` | built-in or `go get` |

## Property Test Templates

### Roundtrip
```
for all x in domain:
  assert decode(encode(x)) == x
```

### Invariant
```
for all x in domain:
  result = transform(x)
  assert invariant(result)  # e.g., is_sorted, len(result) == len(x)
```

### Idempotent
```
for all x in domain:
  assert f(f(x)) == f(x)
```

### No-crash (Robustness)
```
for all x in domain:
  f(x)  # should not throw for any valid input
```

## Integration with TDD Workflow

During the **RED** phase of `/kiro:spec-impl`:
1. Write example-based failing tests first (standard TDD)
2. Evaluate if any function under test matches a property type above
3. If yes, write property-based tests alongside example tests
4. Add spec backlink: `# Verifies: specs/{feature}/requirements.md#N.M (property)`

During **adversarial validation** (`/kiro:validate-adversarial`):
- Functions with data transformations that lack property tests are a finding
- This affects the adversarial score (potential false confidence in correctness)
