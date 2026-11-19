from dataclasses import dataclass


@dataclass(frozen=True)
class AuditStatistics:
    record_count: int
    peak: int | None


def calculate_statistics(records: tuple[int, ...]) -> AuditStatistics:
    return AuditStatistics(len(records), max((abs(record) for record in records), default=None))
