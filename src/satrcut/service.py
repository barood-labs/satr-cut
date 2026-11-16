"""Run the renderer as a service: systemd on Linux, Task Scheduler on Windows.

Install writes the unit files into the project and prints the command to
register them. It does not need administrator rights to write the files.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from satrcut.runner import render_episode


def systemd_unit(project: Path, episode: int, provider: str) -> str:
    executable = Path(sys.executable).as_posix()
    root = project.resolve().as_posix()
    return f"""[Unit]
Description=Satr Cut episode {episode} render
After=network.target

[Service]
Type=simple
WorkingDirectory={root}
ExecStart={executable} -m satrcut service run -e {episode} --provider {provider}
Restart=on-failure
RestartSec=10
Environment=SATRCUT_PROJECT={root}

[Install]
WantedBy=default.target
"""


def windows_task_xml(episode: int, provider: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>satrcut</Command>
      <Arguments>service run -e {episode} --provider {provider}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def install(project: Path, episode: int, provider: str) -> str:
    folder = project / "deploy"
    folder.mkdir(parents=True, exist_ok=True)
    unit = folder / "satrcut-render.service"
    xml = folder / "satrcut-render.xml"
    unit.write_text(systemd_unit(project, episode, provider), encoding="utf-8")
    # Task Scheduler rejects a UTF-8 file that claims to be UTF-16.
    xml.write_bytes(windows_task_xml(episode, provider).encode("utf-16"))
    root = project.resolve()
    return "\n".join(
        [
            f"wrote {unit}",
            f"wrote {xml}",
            "",
            "Linux (user service):",
            f"  mkdir -p ~/.config/systemd/user",
            f"  cp {unit} ~/.config/systemd/user/satrcut-render.service",
            "  systemctl --user daemon-reload",
            "  systemctl --user enable --now satrcut-render.service",
            "",
            "Windows (Task Scheduler, current user):",
            f'  schtasks /Create /TN "SatrCut Render" /XML "{xml.resolve()}" /F',
            "or, without the XML file:",
            f'  schtasks /Create /TN "SatrCut Render" /SC ONLOGON /RL LIMITED /F /TR "satrcut service run -e {episode} --provider {provider}"',
            "",
            "The service runs until the episode is assembled-ready (every shot has a manifest).",
            "A STOP file at .satrcut/STOP halts it and is never deleted.",
            f"Working directory for a manual run: {root}",
        ]
    )


def run_service(project: Path, episode: int, provider: str, *, poll_s: float = 15.0) -> int:
    """Keep rendering until the episode is done, the run fails, or STOP is present."""
    stop = project / ".satrcut" / "STOP"
    while True:
        if stop.exists():
            print("STOP file present; leaving it in place")
            return 0
        code = render_episode(project, episode, provider_name=provider)
        if code == 0:
            print("render complete")
            return 0
        if code == 3 and stop.exists():
            print("STOP file present; leaving it in place")
            return 0
        if code == 3:
            print(f"halted; retrying in {poll_s:.0f}s")
            time.sleep(poll_s)
            continue
        return code
