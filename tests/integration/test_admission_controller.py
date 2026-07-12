import asyncio

import pytest

from src.scan64.chess.analysis.admission import AdmissionConfig, AdmissionController


@pytest.mark.asyncio
async def test_admission_controller_quota():
    controller = AdmissionController(AdmissionConfig(daily_quota_games=2))

    tasks_run = 0

    async def dummy_task():
        nonlocal tasks_run
        await asyncio.sleep(0.01)
        tasks_run += 1
        return tasks_run

    # Submit tasks for player A
    f1 = controller.submit("player_A", dummy_task)
    f2 = controller.submit("player_A", dummy_task)

    # These should be scheduled immediately since quota is 2
    assert controller._get_usage("player_A") == 2
    assert len(controller.queues["player_A"]) == 0

    # Submit 3rd task, should go to queue
    f3 = controller.submit("player_A", dummy_task)
    assert controller._get_usage("player_A") == 2
    assert len(controller.queues["player_A"]) == 1

    # Wait for them all
    await asyncio.gather(f1, f2, f3)

    assert tasks_run == 3

    controller.stop()


@pytest.mark.asyncio
async def test_admission_controller_fair_share():
    controller = AdmissionController(AdmissionConfig(daily_quota_games=0))

    run_order = []

    async def task_a():
        await asyncio.sleep(0.01)
        run_order.append("A")

    async def task_b():
        await asyncio.sleep(0.01)
        run_order.append("B")

    # Queue multiple tasks for A
    f_a1 = controller.submit("player_A", task_a)
    f_a2 = controller.submit("player_A", task_a)

    # Queue task for B
    f_b1 = controller.submit("player_B", task_b)

    await asyncio.gather(f_a1, f_a2, f_b1)

    # With daily_quota=0, all go to fair-share queue.
    # Player A submitted first, so they get 1 task.
    # Then Player B gets 1 task.
    # Then Player A gets their second task.
    assert run_order == ["A", "B", "A"]

    controller.stop()
