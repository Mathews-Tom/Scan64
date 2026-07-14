from datetime import datetime

from sqlmodel import Field, SQLModel


class ReviewSchedule(SQLModel, table=True):
    """
    Spaced-repetition scheduling information for a specific item (e.g. concept or motif).
    """

    player_id: str = Field(primary_key=True)
    item_id: str = Field(primary_key=True)

    next_review_at: datetime
    last_reviewed_at: datetime | None = None

    interval_days: float = 1.0
    ease_factor: float = 2.5

    def is_due(self, current_time: datetime) -> bool:
        """
        Check whether this item is due for review based on next_review_at.
        """
        return current_time >= self.next_review_at

    def update(self, success: bool, current_time: datetime) -> None:
        """
        Update the spaced-repetition intervals and ease factors based on review result.
        Uses a simple SM-2 style algorithm.
        """
        self.last_reviewed_at = current_time

        if success:
            # Increase interval based on ease factor
            self.interval_days *= self.ease_factor
            # Slightly increase ease factor
            self.ease_factor = min(3.0, self.ease_factor + 0.1)
        else:
            # Reset interval
            self.interval_days = 1.0
            # Decrease ease factor, minimum 1.3
            self.ease_factor = max(1.3, self.ease_factor - 0.2)

        # Assuming python's datetime timedelta requires integers or timedelta obj, wait,
        # sqlmodel usually persists datetimes. Let's just store interval_days and compute next.
        from datetime import timedelta

        self.next_review_at = current_time + timedelta(days=self.interval_days)
