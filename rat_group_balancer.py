#!/usr/bin/env python3
"""
Build equal-sized rat groups from a CSV while balancing per-column averages.

Input CSV format:
- A `Name` column first.
- Any number of numeric columns after `Name`.
- One rat per row.

A grouping is valid when, for every numeric column, the difference between the
largest and smallest group average is <= that column's delta.

Examples:
  python rat_group_balancer.py rats.csv --groups 3 --deltas 5,2,1.5
  python rat_group_balancer.py rats.csv --groups 4 \
      --deltas "Weight=5,Age=2,3=1.0"
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sys
import time
from dataclasses import dataclass
from typing import Iterable


class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"\nArgument error: {message}\n", file=sys.stderr)
        self.print_help(sys.stderr)
        raise SystemExit(2)


@dataclass
class RatRecord:
    name: str
    raw_row: dict[str, str]
    values: list[float]


@dataclass
class Evaluation:
    score: float
    max_pairwise_diffs: list[float]
    avg_pairwise_diffs: list[float]
    means_by_group: list[list[float]]


def read_rats_csv(
    path: str,
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
    name_column: str = "Name",
) -> tuple[list[str], list[str], list[RatRecord]]:
    with open(path, "r", newline="", encoding=encoding) as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("Input CSV is missing a header row.")

        raw_headers = list(reader.fieldnames)
        headers = [h.strip() for h in raw_headers]
        if not headers:
            raise ValueError("Input CSV header row is empty.")
        if len(set(h.lower() for h in headers)) != len(headers):
            raise ValueError("CSV has duplicate column names (case-insensitive).")

        header_lookup = {h.lower(): h for h in headers}
        if name_column.lower() not in header_lookup:
            raise ValueError(
                f"Missing required name column '{name_column}'. "
                f"Found columns: {', '.join(headers)}"
            )

        normalized_name_header = header_lookup[name_column.lower()]
        name_index = headers.index(normalized_name_header)
        if name_index != 0:
            raise ValueError(
                f"Name column '{normalized_name_header}' must be the first column."
            )

        metric_headers = headers[1:]
        if not metric_headers:
            raise ValueError(
                "CSV must include at least one numeric metric column after Name."
            )

        rats: list[RatRecord] = []
        for row_num, row in enumerate(reader, start=2):
            normalized_row = {
                clean_header: (row.get(raw_header) or "")
                for raw_header, clean_header in zip(raw_headers, headers)
            }

            name = normalized_row[normalized_name_header].strip()
            if not name:
                raise ValueError(f"Row {row_num}: name is empty.")

            values: list[float] = []
            for metric_name in metric_headers:
                raw = normalized_row[metric_name].strip()
                if raw == "":
                    raise ValueError(
                        f"Row {row_num}, column '{metric_name}' is empty."
                    )
                try:
                    values.append(float(raw))
                except ValueError as exc:
                    raise ValueError(
                        f"Row {row_num}, column '{metric_name}' is not numeric: '{raw}'"
                    ) from exc

            rats.append(RatRecord(name=name, raw_row=normalized_row, values=values))

    if not rats:
        raise ValueError("Input CSV contains no rat rows.")
    return headers, metric_headers, rats


def _parse_numeric_list(values: Iterable[str]) -> list[float]:
    parsed: list[float] = []
    for raw in values:
        item = raw.strip()
        if not item:
            continue
        try:
            value = float(item)
        except ValueError as exc:
            raise ValueError(f"Invalid delta value '{item}'.") from exc
        if value < 0:
            raise ValueError("Delta values must be >= 0.")
        parsed.append(value)
    if not parsed:
        raise ValueError("No delta values were provided.")
    return parsed


def parse_deltas(delta_spec: str, metric_headers: list[str]) -> list[float]:
    """
    Supports:
    - Numeric list by column order: "5,2,1.5" (or single "2.0" for all columns)
    - Name/index mapping: "Weight=5,Age=2,3=1.5" (index is 1-based metric index)
    """
    parts = [p.strip() for p in delta_spec.split(",") if p.strip()]
    if not parts:
        raise ValueError("`--deltas` cannot be empty.")

    has_equals = [("=" in p) for p in parts]
    if all(has_equals):
        deltas: list[float | None] = [None] * len(metric_headers)
        by_name = {name.lower(): idx for idx, name in enumerate(metric_headers)}

        for part in parts:
            raw_key, raw_value = part.split("=", 1)
            key = raw_key.strip()
            if not key:
                raise ValueError(f"Invalid delta mapping '{part}'.")

            try:
                value = float(raw_value.strip())
            except ValueError as exc:
                raise ValueError(f"Invalid delta value in '{part}'.") from exc
            if value < 0:
                raise ValueError("Delta values must be >= 0.")

            if key.isdigit():
                idx = int(key) - 1
                if idx < 0 or idx >= len(metric_headers):
                    raise ValueError(
                        f"Delta column index {key} is out of range "
                        f"(1-{len(metric_headers)})."
                    )
            else:
                lowered = key.lower()
                if lowered not in by_name:
                    raise ValueError(
                        f"Unknown metric column '{key}'. "
                        f"Known columns: {', '.join(metric_headers)}"
                    )
                idx = by_name[lowered]

            deltas[idx] = value

        missing = [metric_headers[i] for i, value in enumerate(deltas) if value is None]
        if missing:
            raise ValueError(
                "Delta mapping must define all metric columns. Missing: "
                + ", ".join(missing)
            )
        return [float(v) for v in deltas]

    if any(has_equals):
        raise ValueError(
            "Mixed delta formats are not allowed. Use either all numbers or all key=value."
        )

    values = _parse_numeric_list(parts)
    if len(values) == 1:
        return [values[0]] * len(metric_headers)
    if len(values) != len(metric_headers):
        raise ValueError(
            f"Delta count ({len(values)}) must be 1 or match number of "
            f"metric columns ({len(metric_headers)})."
        )
    return values


def evaluate_groups(
    groups: list[list[int]],
    data_matrix: list[list[float]],
    deltas: list[float],
) -> Evaluation:
    group_count = len(groups)
    metric_count = len(deltas)

    means_by_group: list[list[float]] = [[0.0] * metric_count for _ in range(group_count)]
    for group_idx, members in enumerate(groups):
        if not members:
            raise ValueError("Internal error: group with zero members.")

        denom = float(len(members))
        sums = [0.0] * metric_count
        for rat_idx in members:
            row = data_matrix[rat_idx]
            for col_idx in range(metric_count):
                sums[col_idx] += row[col_idx]
        for col_idx in range(metric_count):
            means_by_group[group_idx][col_idx] = sums[col_idx] / denom

    max_pairwise_diffs = [0.0] * metric_count
    avg_pairwise_diffs = [0.0] * metric_count
    score = 0.0

    for col_idx in range(metric_count):
        col_means = [means_by_group[group_idx][col_idx] for group_idx in range(group_count)]

        max_diff = 0.0
        pairwise_excess_total = 0.0
        pairwise_diff_total = 0.0
        pair_count = 0
        for i in range(group_count):
            for j in range(i + 1, group_count):
                diff = abs(col_means[i] - col_means[j])
                pairwise_diff_total += diff
                pair_count += 1
                if diff > max_diff:
                    max_diff = diff
                excess = diff - deltas[col_idx]
                if excess > 0:
                    pairwise_excess_total += excess

        max_pairwise_diffs[col_idx] = max_diff
        avg_pairwise_diffs[col_idx] = pairwise_diff_total / pair_count if pair_count else 0.0

        # Score each column by average pairwise violation across group means.
        # Score == 0 means every pair is within the column delta.
        score += pairwise_excess_total / pair_count if pair_count else 0.0

    return Evaluation(
        score=score,
        max_pairwise_diffs=max_pairwise_diffs,
        avg_pairwise_diffs=avg_pairwise_diffs,
        means_by_group=means_by_group,
    )


def evaluation_sort_key(eval_result: Evaluation, deltas: list[float]) -> tuple[float, float, float]:
    """
    Lower is better.
    1) Constraint violation score (0 means fully valid).
    2) Normalized spread sum (scale-aware tie-breaker).
    3) Raw spread sum.
    """
    normalized_avg_pairwise_sum = 0.0
    normalized_max_pairwise_sum = 0.0
    for avg_diff, max_diff, delta in zip(
        eval_result.avg_pairwise_diffs, eval_result.max_pairwise_diffs, deltas
    ):
        if delta > 0:
            normalized_avg_pairwise_sum += avg_diff / delta
            normalized_max_pairwise_sum += max_diff / delta
        elif avg_diff == 0 and max_diff == 0:
            normalized_avg_pairwise_sum += 0.0
            normalized_max_pairwise_sum += 0.0
        else:
            normalized_avg_pairwise_sum += float("inf")
            normalized_max_pairwise_sum += float("inf")
    return (
        eval_result.score,
        normalized_avg_pairwise_sum,
        normalized_max_pairwise_sum,
    )


def initial_groups(
    rat_count: int, group_count: int, rng: random.Random
) -> list[list[int]]:
    indices = list(range(rat_count))
    rng.shuffle(indices)

    group_size = rat_count // group_count
    groups: list[list[int]] = []
    cursor = 0
    for _ in range(group_count):
        groups.append(indices[cursor : cursor + group_size])
        cursor += group_size
    return groups


def deep_copy_groups(groups: list[list[int]]) -> list[list[int]]:
    return [members[:] for members in groups]


def find_balanced_groups(
    data_matrix: list[list[float]],
    group_count: int,
    deltas: list[float],
    *,
    max_restarts: int,
    steps_per_restart: int,
    optimize_seconds: float,
    rng: random.Random,
) -> tuple[list[list[int]] | None, Evaluation]:
    rat_count = len(data_matrix)
    best_groups: list[list[int]] | None = None
    best_eval: Evaluation | None = None

    timed_mode = optimize_seconds > 0
    deadline = time.monotonic() + optimize_seconds if timed_mode else None

    restart_count = 0
    while True:
        if timed_mode:
            if deadline is not None and time.monotonic() >= deadline:
                break
        elif restart_count >= max_restarts:
            break

        restart_count += 1
        groups = initial_groups(rat_count, group_count, rng)
        current_eval = evaluate_groups(groups, data_matrix, deltas)
        if best_eval is None or evaluation_sort_key(current_eval, deltas) < evaluation_sort_key(
            best_eval, deltas
        ):
            best_eval = current_eval
            best_groups = deep_copy_groups(groups)
            if best_eval.score == 0 and not timed_mode:
                return best_groups, best_eval

        temperature = 1.0
        for _ in range(steps_per_restart):
            if timed_mode:
                if deadline is not None and time.monotonic() >= deadline:
                    break
            elif current_eval.score == 0:
                return groups, current_eval

            g1, g2 = rng.sample(range(group_count), 2)
            i1 = rng.randrange(len(groups[g1]))
            i2 = rng.randrange(len(groups[g2]))

            groups[g1][i1], groups[g2][i2] = groups[g2][i2], groups[g1][i1]
            trial_eval = evaluate_groups(groups, data_matrix, deltas)

            improved = trial_eval.score <= current_eval.score
            accept_worse = False
            if not improved:
                delta = trial_eval.score - current_eval.score
                accept_prob = math.exp(-delta / max(temperature, 1e-9))
                accept_worse = rng.random() < accept_prob

            if improved or accept_worse:
                current_eval = trial_eval
                if best_eval is None or evaluation_sort_key(current_eval, deltas) < evaluation_sort_key(
                    best_eval, deltas
                ):
                    best_eval = current_eval
                    best_groups = deep_copy_groups(groups)
                    if best_eval.score == 0 and not timed_mode:
                        return best_groups, best_eval
            else:
                groups[g1][i1], groups[g2][i2] = groups[g2][i2], groups[g1][i1]

            temperature *= 0.9995

    if best_eval is None:
        raise RuntimeError("Internal error: search produced no evaluations.")
    return best_groups, best_eval


def write_grouped_output(
    output_path: str,
    headers: list[str],
    rats: list[RatRecord],
    groups: list[list[int]],
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> None:
    with open(output_path, "w", newline="", encoding=encoding) as f:
        fieldnames = ["Group"] + headers
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()

        for group_idx, members in enumerate(groups, start=1):
            for rat_idx in sorted(members, key=lambda idx: rats[idx].name.lower()):
                row = {"Group": f"G{group_idx}"}
                row.update(rats[rat_idx].raw_row)
                writer.writerow(row)


def write_group_summary_csv(
    output_path: str,
    metric_headers: list[str],
    deltas: list[float],
    eval_result: Evaluation,
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> None:
    with open(output_path, "w", newline="", encoding=encoding) as f:
        fieldnames = ["Group"] + metric_headers
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()

        for group_idx, means in enumerate(eval_result.means_by_group, start=1):
            row = {"Group": f"G{group_idx}"}
            for col_idx, metric in enumerate(metric_headers):
                row[metric] = f"{means[col_idx]:.6f}"
            writer.writerow(row)

        spread_row = {"Group": "MaxPairwiseDiff"}
        for col_idx, metric in enumerate(metric_headers):
            spread_row[metric] = f"{eval_result.max_pairwise_diffs[col_idx]:.6f}"
        writer.writerow(spread_row)

        avg_pairwise_row = {"Group": "AvgPairwiseDiff"}
        for col_idx, metric in enumerate(metric_headers):
            avg_pairwise_row[metric] = f"{eval_result.avg_pairwise_diffs[col_idx]:.6f}"
        writer.writerow(avg_pairwise_row)

        delta_row = {"Group": "AllowedDelta"}
        for col_idx, metric in enumerate(metric_headers):
            delta_row[metric] = f"{deltas[col_idx]:.6f}"
        writer.writerow(delta_row)


def format_col_report(
    metric_headers: list[str],
    max_pairwise_diffs: list[float],
    avg_pairwise_diffs: list[float],
    deltas: list[float],
) -> str:
    lines = []
    for col_idx, metric in enumerate(metric_headers):
        ok = max_pairwise_diffs[col_idx] <= deltas[col_idx] + 1e-12
        status = "OK" if ok else "FAIL"
        lines.append(
            f"  - {metric}: avg_pairwise={avg_pairwise_diffs[col_idx]:.6f}, "
            f"max_pairwise={max_pairwise_diffs[col_idx]:.6f}, "
            f"delta={deltas[col_idx]:.6f} [{status}]"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = FriendlyArgumentParser(
        description=(
            "Split rats into equal-sized groups while keeping each metric's group "
            "averages within per-column delta limits."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python rat_group_balancer.py rats.csv --groups 3 --deltas 5,2,1.5\n"
            "  python rat_group_balancer.py rats.csv --groups 4 --deltas \"Weight=5,Age=2,3=1.0\"\n"
            "  python rat_group_balancer.py rats.csv --groups 5 --deltas 3 --optimize-seconds 10\n"
        ),
    )
    parser.add_argument("input_csv", help="Path to input CSV (Name + numeric metric columns).")
    parser.add_argument(
        "--groups",
        type=int,
        required=True,
        help="Number of equal-sized groups to create.",
    )
    parser.add_argument(
        "--deltas",
        required=True,
        help=(
            "Per-column delta spec. Either comma-separated numeric list by metric order "
            "(e.g. 5,2,1.5), a single number for all columns (e.g. 2), or key=value "
            "pairs by metric name or 1-based metric index (e.g. Weight=5,Age=2,3=1.5)."
        ),
    )
    parser.add_argument(
        "--output",
        default="grouped_rats.csv",
        help="Output CSV path for grouped rats (default: grouped_rats.csv).",
    )
    parser.add_argument(
        "--summary-output",
        default="group_summary.csv",
        help="Output CSV path for group means/spreads (default: group_summary.csv).",
    )
    parser.add_argument(
        "--name-column",
        default="Name",
        help="Name column header (default: Name).",
    )
    parser.add_argument(
        "--delimiter",
        default=",",
        help="CSV delimiter for input/output (default: ',').",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="File encoding for input/output (default: utf-8).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible group search.",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=200,
        help="Number of random restarts for the search (default: 200).",
    )
    parser.add_argument(
        "--steps-per-restart",
        type=int,
        default=10000,
        help="Swap attempts per restart (default: 10000).",
    )
    parser.add_argument(
        "--optimize-seconds",
        type=float,
        default=0.0,
        help=(
            "If > 0, keep searching for the best grouping for up to this many seconds "
            "instead of stopping on the first valid solution."
        ),
    )
    return parser


def fail_with_help(parser: argparse.ArgumentParser, message: str, exit_code: int = 2) -> int:
    print(f"Input error: {message}\n", file=sys.stderr)
    parser.print_help(sys.stderr)
    return exit_code


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.groups <= 0:
        return fail_with_help(parser, "`--groups` must be > 0.")
    if args.max_restarts <= 0 or args.steps_per_restart <= 0:
        return fail_with_help(
            parser,
            "`--max-restarts` and `--steps-per-restart` must be > 0.",
        )
    if args.optimize_seconds < 0:
        return fail_with_help(parser, "`--optimize-seconds` must be >= 0.")

    try:
        headers, metric_headers, rats = read_rats_csv(
            args.input_csv,
            delimiter=args.delimiter,
            encoding=args.encoding,
            name_column=args.name_column,
        )
        deltas = parse_deltas(args.deltas, metric_headers)
    except ValueError as exc:
        return fail_with_help(parser, str(exc))

    rat_count = len(rats)
    if args.groups > rat_count:
        return fail_with_help(
            parser,
            f"Cannot create {args.groups} groups from {rat_count} rats.",
        )
    if rat_count % args.groups != 0:
        return fail_with_help(
            parser,
            f"Rat count ({rat_count}) is not divisible by number of groups ({args.groups}); "
            "equal-sized groups are impossible.",
        )

    rng = random.Random(args.seed)
    data_matrix = [rat.values for rat in rats]

    groups, eval_result = find_balanced_groups(
        data_matrix,
        args.groups,
        deltas,
        max_restarts=args.max_restarts,
        steps_per_restart=args.steps_per_restart,
        optimize_seconds=args.optimize_seconds,
        rng=rng,
    )

    if groups is None or eval_result.score > 0:
        print(
            "No valid grouping found with current search limits. "
            "Try increasing --max-restarts and/or --steps-per-restart, "
            "or relaxing delta values.",
            file=sys.stderr,
        )
        print(
            "Best attempt column report:\n"
            + format_col_report(
                metric_headers,
                eval_result.max_pairwise_diffs,
                eval_result.avg_pairwise_diffs,
                deltas,
            ),
            file=sys.stderr,
        )
        return 1

    write_grouped_output(
        args.output,
        headers,
        rats,
        groups,
        delimiter=args.delimiter,
        encoding=args.encoding,
    )
    write_group_summary_csv(
        args.summary_output,
        metric_headers,
        deltas,
        eval_result,
        delimiter=args.delimiter,
        encoding=args.encoding,
    )

    print("Balanced grouping found.")
    print(f"Rats: {rat_count}, Groups: {args.groups}, Group size: {rat_count // args.groups}")
    print(f"Grouped output: {args.output}")
    print(f"Summary output: {args.summary_output}")
    if args.optimize_seconds > 0:
        print(f"Optimization window: {args.optimize_seconds:.3f} seconds")
    print("Column checks:")
    print(
        format_col_report(
            metric_headers,
            eval_result.max_pairwise_diffs,
            eval_result.avg_pairwise_diffs,
            deltas,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
