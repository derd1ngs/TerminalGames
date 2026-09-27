"""The story catalog: every shipped story with what a story list shows --
title, publishing date, description, size, and endings found -- sorted
newest first. Both frontends use it, so they list stories identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .endings import found_endings
from .loader import discover_stories
from .story import Story

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class StoryInfo:
    ref: str  # the story's directory name, as accepted on the command line
    id: str
    title: str
    published: Optional[date]
    description: str
    chapters: int
    endings: int
    endings_found: int
    series: str = ""
    part: Optional[int] = None
    hat: str = ""

    @property
    def series_label(self) -> str:
        """e.g. "The Seven Hats · Part 1 · Black Hat" ("" outside a series)."""
        if not self.series:
            return ""
        parts = [self.series]
        if self.part:
            parts.append(f"Part {self.part}")
        if self.hat:
            parts.append(f"{self.hat.capitalize()} Hat")
        return " · ".join(parts)

    @property
    def published_label(self) -> str:
        """e.g. "8 Sep 2026" -- fixed English month names, not the system
        locale, so every frontend and machine shows the same thing."""
        if self.published is None:
            return ""
        return f"{self.published.day} {_MONTHS[self.published.month - 1]} {self.published.year}"

    @property
    def summary(self) -> str:
        """e.g. "Published 8 Sep 2026 · 1 chapter · 4 endings · 2/4 found"."""
        parts = [f"Published {self.published_label}"] if self.published else []
        parts.append(f"{self.chapters} chapter{'s' if self.chapters != 1 else ''}")
        parts.append(f"{self.endings} ending{'s' if self.endings != 1 else ''}")
        if self.endings_found:
            parts.append(f"{self.endings_found}/{self.endings} found")
        return " · ".join(parts)


def story_info(story_dir: Path, saves_root: Path) -> StoryInfo:
    story = Story.load(story_dir)
    ending_ids = [s.id for ch in story.chapters.values() for s in ch.scenes.values() if s.type == "ending"]
    found = found_endings(saves_root / story.id)
    return StoryInfo(
        ref=story_dir.name,
        id=story.id,
        title=story.title,
        published=story.published,
        description=story.description,
        chapters=len(story.chapters),
        endings=len(ending_ids),
        endings_found=sum(1 for e in ending_ids if e in found),
        series=story.series,
        part=story.part,
        hat=story.hat,
    )


def catalog(saves_root: Path, stories_dir: Optional[Path] = None) -> list[StoryInfo]:
    """Every story, newest first; undated stories last. Parts of a series
    published the same day keep their order; otherwise by title."""
    dirs = discover_stories() if stories_dir is None else discover_stories(stories_dir)
    infos = [story_info(d, saves_root) for d in dirs]
    return sorted(
        infos,
        key=lambda i: (
            i.published is None,
            -(i.published or date.min).toordinal(),
            i.series,
            i.part or 0,
            i.title,
        ),
    )
