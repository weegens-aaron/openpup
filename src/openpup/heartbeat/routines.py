"""Scheduled jobs — reminders + cron-style agent tasks delivered to a platform.

On each heartbeat tick, due jobs fire:
* a **message** job delivers its text verbatim (a reminder);
* a **prompt** job runs through the agent and delivers the output (unless the
  agent emits the ``[SILENT]`` sentinel, mirroring hermes' no-spam pattern).

Delivery defaults to the owner's address when a job has no explicit target.
"""

from __future__ import annotations

import logging
from typing import List

from openpup import transcripts
from openpup.agent_host import AgentHost
from openpup.config import Settings
from openpup.heartbeat.scheduler import Scheduler
from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import PlatformRegistry

logger = logging.getLogger("openpup.routines")


async def run_due_routines(
    host: AgentHost,
    settings: Settings,
    registry: PlatformRegistry,
    scheduler: Scheduler,
) -> List[str]:
    """Fire every due job and deliver results. Returns names that fired."""
    fired: List[str] = []
    for job in scheduler.due():
        target = job.deliver or settings.owner_address
        try:
            if job.message:
                # Plain reminder: deliver the text verbatim.
                if target:
                    await registry.send(Envelope.to(target, job.message))
                else:
                    logger.warning("Job '%s' has no delivery target", job.name)
            else:
                # Agent task: run the prompt, deliver the output.
                logger.info("Running scheduled task '%s'", job.name)
                # Transcript: "heartbeat:routines:YYYYMMDD" — the job prompt is
                # user-authored, so it goes in as the 'user' turn.
                session_id = transcripts.heartbeat_session_id("routines")
                transcripts.record_turn(
                    session_id, transcripts.HEARTBEAT_SOURCE, "user", job.prompt
                )
                output = (
                    await host.run(
                        job.prompt,
                        conversation=f"__routine__:{job.name}",
                        keep_history=False,
                    )
                    or ""
                ).strip()
                transcripts.record_turn(
                    session_id, transcripts.HEARTBEAT_SOURCE, "assistant", output
                )
                if output and "[SILENT]" not in output and target:
                    await registry.send(Envelope.to(target, output))
            fired.append(job.name)
        except Exception:
            logger.exception("Scheduled job '%s' failed", job.name)
    return fired
