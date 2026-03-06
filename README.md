# Rat Balancer

`rat_group_balancer.py` builds equal-sized groups of rats from a CSV while balancing each metric column across groups.

For each numeric column, the script checks pairwise group averages:

`abs(group_average_i - group_average_j) <= allowed_delta_for_that_column` for all group pairs `i, j`.

## Requirements

- Python 3.9+

## Input CSV Format

- First column must be `Name` (or set `--name-column`).
- Every column after `Name` must be numeric.
- One rat per row.

Example:

```csv
Name,Weight,Age,Score
Rat001,243,9,52
Rat002,246,10,59
Rat003,249,11,66
```

## Basic Usage

```bash
python3 rat_group_balancer.py input.csv --groups 4 --deltas 5,2,3
```

This writes:

- `grouped_rats.csv`: original rows + `Group` column (`G1`, `G2`, ...)
- `group_summary.csv`: group averages + `MaxPairwiseDiff` row + `AvgPairwiseDiff` row + `AllowedDelta` row

## Delta Formats

You can provide deltas 3 different ways:

1. Per column by order:

```bash
--deltas 5,2,3
```

2. One value for all metric columns:

```bash
--deltas 3
```

3. By column name or 1-based metric index:

```bash
--deltas "Weight=5,Age=2,3=1.5"
```

## Optimize For Time Budget

By default, the script stops at the first valid solution.

Use `--optimize-seconds` to keep searching for a better solution until time runs out:

```bash
python3 rat_group_balancer.py input.csv \
  --groups 10 \
  --deltas 25,10,20,20 \
  --optimize-seconds 5
```

## CLI Options

```text
python3 rat_group_balancer.py INPUT_CSV --groups N --deltas SPEC [options]
```

- `--groups`: number of groups to create (must divide total rat count exactly)
- `--deltas`: delta spec (see formats above)
- `--output`: grouped output CSV path (default `grouped_rats.csv`)
- `--summary-output`: summary output CSV path (default `group_summary.csv`)
- `--name-column`: name column header (default `Name`)
- `--delimiter`: CSV delimiter (default `,`)
- `--encoding`: file encoding (default `utf-8`)
- `--seed`: random seed for reproducible results
- `--max-restarts`: random restarts for solver (default `200`)
- `--steps-per-restart`: swap attempts per restart (default `10000`)
- `--optimize-seconds`: if `> 0`, search for best solution up to X seconds

## Included Test Data

Files in `test_csv/`:

- `possible_two_groups.csv`
- `possible_three_groups.csv`
- `possible_four_groups.csv`
- `impossible_strict_delta.csv`
- `performance_100.csv`

### Example runs

Valid:

```bash
python3 rat_group_balancer.py test_csv/possible_three_groups.csv --groups 3 --deltas 5,2,3
```

Impossible with strict weight delta:

```bash
python3 rat_group_balancer.py test_csv/impossible_strict_delta.csv --groups 2 --deltas 4,0
```

100-row performance test:

```bash
python3 rat_group_balancer.py test_csv/performance_100.csv --groups 10 --deltas 25,10,20,20
```

100-row performance test with max-time optimization (search for best up to 10 seconds):

```bash
python3 rat_group_balancer.py test_csv/performance_100.csv --groups 10 --deltas 25,10,20,20 --optimize-seconds 10
```

## Exit Codes

- `0`: success (valid grouping found)
- `1`: no valid grouping found under current constraints/search budget
- `2`: input or argument error
