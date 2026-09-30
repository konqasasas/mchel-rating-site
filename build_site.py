#!/usr/bin/env python3
"""Build the CHELCY SPEEDRUN RATING static GitHub Pages site."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
SITE_TITLE = "CHELCY SPEEDRUN RATING"
BEST_N = 30
DISPLAY_N = 40

TIER_ORDER = [
    "Grandmaster", "Master I", "Master II", "Diamond I", "Diamond II",
    "Platinum I", "Platinum II", "Gold I", "Gold II", "Silver I",
    "Silver II", "Bronze I", "Bronze II", "Iron",
]
GRADE_ORDER = ["SS", "S+", "S", "A+", "A", "B+", "B", "C+", "C"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--overall-csv", type=Path, required=True)
    p.add_argument("--top40-components-csv", type=Path, required=True)
    p.add_argument("--player-course-records-csv", type=Path, required=True)
    p.add_argument("--metadata-json", type=Path, required=True)
    p.add_argument("--course-difficulty-csv", type=Path, required=True)
    p.add_argument("--course-records-csv", type=Path, required=True)
    p.add_argument("--rankings-csv", type=Path, required=True)
    p.add_argument("--snapshot-summary", type=Path, required=True)
    p.add_argument("--history-dir", type=Path, default=Path("data/history"))
    p.add_argument("--activity-log", type=Path, default=Path("data/activity/events.jsonl"))
    p.add_argument("--out-dir", type=Path, default=Path("docs"))
    return p.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


def fnum(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def inum(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def fmt_time(ms: Any) -> str:
    n = inum(ms)
    if n <= 0:
        return "—"
    m, r = divmod(n, 60_000)
    s, x = divmod(r, 1_000)
    return f"{m}:{s:02d}.{x:03d}"


def fmt_diff(ms: int) -> str:
    if ms == 0:
        return "+0.000"
    sign = "+" if ms > 0 else "-"
    n = abs(ms)
    m, r = divmod(n, 60_000)
    s, x = divmod(r, 1_000)
    if m:
        return f"{sign}{m}:{s:02d}.{x:03d}"
    return f"{sign}{s}.{x:03d}"


def tier_for_rating(r: float) -> str:
    if r >= 1300: return "Grandmaster"
    if r >= 1250: return "Master I"
    if r >= 1200: return "Master II"
    if r >= 1150: return "Diamond I"
    if r >= 1100: return "Diamond II"
    if r >= 1050: return "Platinum I"
    if r >= 1000: return "Platinum II"
    if r >= 950: return "Gold I"
    if r >= 900: return "Gold II"
    if r >= 850: return "Silver I"
    if r >= 800: return "Silver II"
    if r >= 750: return "Bronze I"
    if r >= 700: return "Bronze II"
    return "Iron"


def grade_for_raw(raw: float) -> str:
    if math.isclose(raw, 100.0, abs_tol=1e-9): return "SS"
    if raw >= 99.5: return "S+"
    if raw >= 99.0: return "S"
    if raw >= 98.0: return "A+"
    if raw >= 96.5: return "A"
    if raw >= 95.0: return "B+"
    if raw >= 92.5: return "B"
    if raw >= 90.0: return "C+"
    return "C"


def slug(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", v.lower()).strip("-")


def tier_badge(tier: str) -> str:
    return f'<span class="tier tier-{slug(tier)}">{esc(tier)}</span>'


def grade_badge(grade: str) -> str:
    return f'<span class="grade grade-{slug(grade)}">{esc(grade)}</span>'


def course_url(name: str) -> str:
    return "https://www.mchel.net/info#athletic-1.12.2:ranking:" + quote(name, safe="")


def player_url(name: str) -> str:
    return "https://www.mchel.net/player/" + quote(name, safe="_-.")


def snapshot_info(path: Path) -> tuple[datetime, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    stamp = data.get("fetched_at_utc") or data.get("source_run_fetched_at_utc")
    dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(JST)
    return dt, dt.date().isoformat()


def difficulty_date(metadata: dict[str, Any], fallback: str) -> str:
    version = str(metadata.get("difficulty_version", ""))
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", version)
    return match.group(1) if match else fallback


def load_history(history_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[date, dict[str, dict[str, Any]]]]:
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_day: dict[date, dict[str, dict[str, Any]]] = {}
    for path in sorted(history_dir.glob("*_overall_ranking.csv")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_overall_ranking\.csv$", path.name)
        if not m:
            continue
        d = date.fromisoformat(m.group(1))
        day_rows: dict[str, dict[str, Any]] = {}
        for row in read_csv(path):
            uuid = row.get("player_uuid", "")
            item = {
                "date": m.group(1),
                "rating": fnum(row.get("published_rating")),
                "rank": inum(row.get("overall_rank")),
                "name": row.get("player_name", ""),
            }
            by_player[uuid].append(item)
            day_rows[uuid] = item
        by_day[d] = day_rows
    return by_player, by_day


def weekly_reference(current_day: date, by_day: dict[date, dict[str, dict[str, Any]]]) -> tuple[date | None, dict[str, dict[str, Any]]]:
    target = current_day - timedelta(days=7)
    candidates = [d for d in by_day if d <= target]
    if not candidates:
        return None, {}
    d = max(candidates)
    return d, by_day[d]


def load_events(path: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        result[str(e.get("player_uuid", ""))].append(e)
    for events in result.values():
        events.sort(key=lambda e: (str(e.get("date", "")), str(e.get("type", ""))), reverse=True)
    return result


def rating_chart(points: list[dict[str, Any]]) -> str:
    if not points:
        return '<div class="chart-empty">No rating history yet.</div>'
    pts = points[-120:]
    ratings = [float(p["rating"]) for p in pts]
    lo, hi = min(ratings), max(ratings)
    if math.isclose(lo, hi):
        lo -= 1; hi += 1
    w, h, px, py = 620, 170, 12, 12
    coords = []
    for i, r in enumerate(ratings):
        x = px if len(ratings) == 1 else px + (w - 2*px) * i / (len(ratings)-1)
        y = py + (h - 2*py) * (hi-r)/(hi-lo)
        coords.append(f"{x:.1f},{y:.1f}")
    return f'''<svg class="rating-svg" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" aria-label="Rating history">
      <line x1="0" y1="{h-1}" x2="{w}" y2="{h-1}" class="chart-axis"/>
      <polyline points="{' '.join(coords)}" class="rating-line"/>
    </svg>'''


def relative_last_pb(epoch: Any, now: datetime) -> str:
    value = fnum(epoch, 0)
    if value <= 0:
        return "—"
    if value > 10_000_000_000:
        value /= 1000
    try:
        dt = datetime.fromtimestamp(value, tz=timezone.utc).astimezone(JST)
    except (OSError, OverflowError, ValueError):
        return "—"
    days = max(0, (now.date() - dt.date()).days)
    if days == 0: return "Today"
    if days == 1: return "1 day ago"
    return f"{days} days ago"


def nav(depth: int = 0, current: str = "") -> str:
    pre = "../" * depth
    def item(href: str, text: str, key: str) -> str:
        cls = "active" if current == key else ""
        return f'<a class="{cls}" href="{pre}{href}">{text}</a>'
    return f'''<header class="site-header"><div class="site-header-inner">
      <a class="brand" href="{pre}index.html">CHECLY SPEEDRUN RATING</a>
      <nav class="nav">{item("index.html","Players","players")}{item("courses.html","Courses","courses")}{item("about.html","About Rating","about")}</nav>
    </div></header>'''


def shell(title: str, body: str, *, depth: int = 0, current: str = "", extra_head: str = "") -> str:
    pre = "../" * depth
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — {SITE_TITLE}</title><link rel="stylesheet" href="{pre}static/style.css">{extra_head}<script defer src="{pre}static/app.js"></script></head>
<body>{nav(depth,current)}<main class="page">{body}</main></body></html>'''


def build_index(overall: list[dict[str,str]], history_by_player: dict[str,list[dict[str,Any]]], weekly: dict[str,dict[str,Any]], metadata: dict[str,Any], data_day: str, diff_day: str) -> str:
    rows=[]
    counts=Counter()
    for p in overall:
        rating=fnum(p.get("published_rating")); tier=tier_for_rating(rating); counts[tier]+=1
        uuid=p["player_uuid"]; hist=history_by_player.get(uuid,[])
        peak=max([rating]+[float(x["rating"]) for x in hist])
        old=weekly.get(uuid)
        if old:
            dr=rating-float(old["rating"]); move=int(old["rank"])-inum(p.get("overall_rank"))
            weekly_rating=f'{dr:+.2f}' if round(dr,2)!=0 else '±0.00'
            weekly_class='positive' if dr>0 else 'negative' if dr<0 else 'neutral'
            if move>0: wrank=f'▲{move}'
            elif move<0: wrank=f'▼{abs(move)}'
            else: wrank='—'
            wrank_class='positive' if move>0 else 'negative' if move<0 else 'neutral'
            wrank_sort=move
        else:
            weekly_rating='—'; weekly_class='neutral'; wrank='NEW'; wrank_class='positive'; wrank_sort=999
        skin=f'https://mc-heads.net/head/{quote(uuid,safe="-")}/left'
        rows.append(f'''<tr class="data-row" data-search="{esc(p.get('player_name','')).lower()}" data-tier="{esc(tier)}"
 data-rank="{inum(p.get('overall_rank'))}" data-player="{esc(p.get('player_name','')).lower()}" data-rating="{rating}" data-weekly-rating="{(rating-float(old['rating'])) if old else -999999}" data-weekly-rank="{wrank_sort}" data-peak="{peak}">
<td class="rank-cell">#{inum(p.get('overall_rank'))}</td><td><a class="player-cell" href="players/{esc(uuid)}.html"><img src="{skin}" alt="" onerror="this.style.visibility='hidden'"><strong>{esc(p.get('player_name',''))}</strong><span>›</span></a></td>
<td>{tier_badge(tier)}</td><td class="num strong">{rating:.2f}</td><td class="num {weekly_class}">{weekly_rating}</td><td class="num {wrank_class}">{wrank}</td><td class="num">{peak:.2f}</td></tr>''')
    max_count=max(counts.values()) if counts else 1
    bars=''.join(f'''<div class="tier-bar"><div class="tier-bar-area"><div class="tier-bar-fill tier-bg-{slug(t)}" style="height:{max(3,100*counts[t]/max_count):.1f}%"></div></div><div class="tier-count">{counts[t]}</div><div class="tier-mini tier-{slug(t)}">{esc(t)}</div></div>''' for t in TIER_ORDER if counts[t]>0)
    tier_opts=''.join(f'<option value="{esc(t)}">{esc(t)}</option>' for t in TIER_ORDER)
    body=f'''<section class="hero-grid"><article class="card hero-main"><p class="eyebrow">Leaderboard · 1.12.2</p><h1>Chelcy Speedrun Rating</h1>
<p class="hero-copy">Unofficial overall rating for Chelcy 1.12.2, calculated from each player's best course records.</p>
<div class="update-meta"><div class="update-chip"><span>Data Updated</span><strong>{esc(data_day)}</strong></div><div class="update-chip"><span>Difficulty Updated</span><strong>{esc(diff_day)}</strong></div></div>
<div class="hero-stats"><div class="hero-stat"><span>Players</span><strong>{len(overall)}</strong></div><div class="hero-stat"><span>Eligible Courses</span><strong>{esc(metadata.get('eligible_course_count','—'))}</strong></div></div></article>
<aside class="card tier-chart-card"><h2 class="section-title">Tier Distribution</h2><div class="tier-chart">{bars}</div></aside></section>
<section class="card table-card"><div class="table-head"><h2>Overall Ranking</h2><div class="filters"><input id="player-search" class="control" type="search" placeholder="Search player"><select id="tier-filter" class="control"><option value="">All tiers</option>{tier_opts}</select></div></div>
<div class="table-wrap"><table id="ranking-table"><thead><tr><th data-sort="rank">Rank</th><th data-sort="player">Player</th><th data-sort="tier">Tier</th><th data-sort="rating">Rating</th><th data-sort="weekly-rating">Weekly Rating</th><th data-sort="weekly-rank">Weekly Rank</th><th data-sort="peak">Peak</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>'''
    return shell("Leaderboard",body,current="players")


def activity_html(events: list[dict[str,Any]]) -> str:
    if not events:
        return '<div class="empty-note">No recent activity.</div>'
    groups: dict[str,list[dict[str,Any]]] = defaultdict(list)
    for e in events[:12]: groups[str(e.get("date",""))].append(e)
    chunks=[]
    for day in sorted(groups,reverse=True):
        chunks.append(f'<div class="activity-date">{esc(day)}</div>')
        for e in groups[day]:
            typ=e.get("type")
            delta=fnum(e.get("rating_delta")); dtext=f'{delta:+.2f}' if round(delta,2)!=0 else '0.00'; dcls='positive' if delta>0 else 'negative' if delta<0 else 'neutral'
            if typ=="difficulty_update":
                tag='DIFFICULTY UPDATE'; course='Monthly Difficulty Table Update'; detail=f"Rating {fnum(e.get('old_rating')):.2f} → {fnum(e.get('new_rating')):.2f} / monthly recalculation"
            elif typ=="wr_update":
                tag='#1 UPDATE'; course=str(e.get('course_name','')); detail=f"by {e.get('updater_name','Unknown')} / #1 {fmt_time(e.get('old_wr_ms'))} → {fmt_time(e.get('new_wr_ms'))} / Raw Score {fnum(e.get('old_raw_score')):.2f} → {fnum(e.get('new_raw_score')):.2f}"
            else:
                course=str(e.get('course_name','')); tag='#1 UPDATE' if typ=='self_wr_update' else 'PB UPDATE'
                parts=[]
                if e.get('old_time_ms') is not None: parts.append(f"{fmt_time(e.get('old_time_ms'))} → {fmt_time(e.get('new_time_ms'))}")
                else: parts.append(fmt_time(e.get('new_time_ms')))
                oldr=e.get('old_rank'); newr=e.get('new_rank'); parts.append(f"#{oldr if oldr else '—'} → #{newr if newr else '—'}")
                parts.append(f"{e.get('old_grade') or '—'} → {e.get('new_grade') or '—'}")
                if e.get('best30_status'): parts.append(str(e['best30_status']))
                if typ=='self_wr_update': parts.append('YOUR COURSE RECORD')
                detail=' / '.join(parts)
            chunks.append(f'''<div class="activity-row"><div class="activity-tag">{esc(tag)}</div><div class="activity-line"><strong>{esc(course)}</strong><span>{esc(detail)}</span></div><div class="activity-delta {dcls}">{dtext}</div></div>''')
    return ''.join(chunks)


def distribution_rows(data: list[tuple[str,int]], kind: str) -> str:
    maxv=max([v for _,v in data] or [1])
    return ''.join(f'''<div class="dist-row"><div class="dist-label">{grade_badge(k) if kind=='grade' else esc(k)}</div><div class="dist-track"><span style="width:{(100*v/maxv if maxv else 0):.1f}%"></span></div><div class="dist-count">{v}</div></div>''' for k,v in data)


def build_player(player: dict[str,str], top40: list[dict[str,str]], records: list[dict[str,str]], history: list[dict[str,Any]], events: list[dict[str,Any]], now: datetime) -> str:
    rating=fnum(player.get('published_rating')); tier=tier_for_rating(rating); uuid=player['player_uuid']; name=player.get('player_name','')
    peak=max([rating]+[float(x['rating']) for x in history]) if history else rating
    top40=sorted(top40,key=lambda x:inum(x.get('best40_position'),999))
    best30=top40[:30]
    grade_counts=Counter((r.get('grade') or grade_for_raw(fnum(r.get('base_course_score')))) for r in best30)
    grade_data=[(g,grade_counts[g]) for g in GRADE_ORDER if grade_counts[g]]
    rank_counts=[('#1',0),('#2–3',0),('#4–6',0),('#7–10',0),('#11+',0)]
    for r in best30:
        k=inum(r.get('rank'))
        idx=0 if k==1 else 1 if k<=3 else 2 if k<=6 else 3 if k<=10 else 4
        rank_counts[idx]=(rank_counts[idx][0],rank_counts[idx][1]+1)
    rows=[]
    for i,r in enumerate(top40,1):
        if i==31: rows.append('<tr class="rating-border"><td colspan="9">RATING BORDER</td></tr>')
        raw=fnum(r.get('base_course_score')); grade=r.get('grade') or grade_for_raw(raw); pb=inum(r.get('time_ms')); wr=inum(r.get('course_record_time_ms'))
        rows.append(f'''<tr class="{'outside-best30' if i>30 else ''}"><td>{i}</td><td><a href="{esc(course_url(r.get('course_name','')))}" target="_blank" rel="noopener">{esc(r.get('course_name',''))}</a></td><td>#{inum(r.get('rank'))}</td><td>{fmt_time(pb)}</td><td>{fmt_diff(pb-wr)}</td><td class="num">{raw:.3f}</td><td>{grade_badge(grade)}</td><td class="num">{fnum(r.get('first_place_difficulty')):.3f}</td><td class="num strong">{fnum(r.get('difficulty_adjusted_course_score')):.3f}</td></tr>''')
    skin=f'https://mc-heads.net/body/{quote(uuid,safe="-")}/left'
    sim_records=[{"course":r.get("course_name",""),"time":inum(r.get("time_ms")),"rank":inum(r.get("rank")),"raw":fnum(r.get("base_course_score")),"grade":r.get("grade") or grade_for_raw(fnum(r.get("base_course_score"))),"adjusted":fnum(r.get("difficulty_adjusted_course_score"))} for r in records]
    sim_data=json.dumps({"uuid":uuid,"rating":rating,"records":sim_records},ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    body=f'''<section class="card player-top"><div class="player-identity"><div class="skin-box"><img src="{skin}" alt="" onerror="this.style.visibility='hidden'"></div><div><p class="eyebrow">Player Profile</p><h1>{esc(name)}</h1><div class="player-actions">{tier_badge(tier)}<a href="{esc(player_url(name))}" target="_blank" rel="noopener">View on Chelcy ↗</a></div></div></div><div class="player-rating"><span>Rating</span><strong>{rating:.2f}</strong></div></section>
<section class="player-dashboard"><div class="card stats-card"><h2 class="section-title">Player Stats</h2><div class="stats-grid"><div><span>Rating</span><strong>{rating:.2f}</strong></div><div><span>Peak Rating</span><strong>{peak:.2f}</strong></div><div><span>Global Rank</span><strong>#{inum(player.get('overall_rank'))}</strong></div><div><span>Ranked Courses</span><strong>{inum(player.get('eligible_course_count'))}</strong></div><div><span>1st Place Records</span><strong>{inum(player.get('first_place_record_count'))}</strong></div><div><span>Best 30 #1</span><strong>{inum(player.get('best30_first_place_count'))}</strong></div><div><span>Last PB</span><strong>{relative_last_pb(player.get('last_pb_epoch'),now)}</strong></div></div></div>
<div class="right-charts"><div class="card chart-card"><div class="chart-head"><h2>Rating History</h2><span>Peak {peak:.2f}</span></div>{rating_chart(history)}</div><div class="card chart-card"><div class="chart-head"><h2>Best 30 Distribution</h2><div class="chart-tabs"><button class="dist-tab active" data-target="grade">Grade</button><button class="dist-tab" data-target="rank">Rank</button></div></div><div id="dist-grade" class="distribution">{distribution_rows(grade_data,'grade')}</div><div id="dist-rank" class="distribution hidden">{distribution_rows(rank_counts,'rank')}</div></div></div></section>
<section class="card section-card"><h2 class="section-title">Recent Activity</h2>{activity_html(events)}</section>
<section class="card section-card"><div class="section-row"><h2 class="section-title">Best Records</h2><span>Top {DISPLAY_N}</span></div><div class="table-wrap"><table class="records-table"><thead><tr><th>#</th><th>Course</th><th>Rank</th><th>PB</th><th>WR Diff</th><th>Raw Score</th><th>Grade</th><th>Difficulty</th><th>Adjusted Score</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section class="card simulator-card" id="rating-simulator"><h2 class="section-title">Rating Simulator</h2><div class="sim-grid"><div class="sim-field"><label>Course</label><select id="sim-course" class="control"></select></div><div class="sim-field"><label>Simulated Time</label><input id="sim-time" class="control" type="text" inputmode="decimal" placeholder="1:23.456"><div class="sim-presets"><button data-offset="-50">-0.05s</button><button data-offset="-100">-0.1s</button><button data-offset="-500">-0.5s</button><button data-offset="50">+0.05s</button><button data-offset="100">+0.1s</button><button data-offset="500">+0.5s</button><button id="sim-wr">WR</button></div></div></div><div class="sim-current"><div><span>Current PB</span><strong id="sim-current-pb">—</strong></div><div><span>Current Rank</span><strong id="sim-current-rank">—</strong></div><div><span>Current Grade</span><strong id="sim-current-grade">—</strong></div><div><span>Difficulty</span><strong id="sim-difficulty">—</strong></div></div><div id="sim-message" class="sim-message"></div><div class="sim-rating"><span>{rating:.2f}</span><b>→</b><strong id="sim-new-rating">{rating:.2f}</strong><em id="sim-delta">+0.00</em></div><div class="sim-results"><div><span>Estimated Rank</span><strong id="sim-rank">—</strong></div><div><span>Raw Score</span><strong id="sim-raw">—</strong></div><div><span>Grade</span><strong id="sim-grade">—</strong></div><div><span>Adjusted Score</span><strong id="sim-adjusted">—</strong></div><div><span>Best 30</span><strong id="sim-best30">—</strong></div></div><div id="sim-status" class="sim-status"></div></section>
<script type="application/json" id="player-simulator-data">{sim_data}</script>'''
    return shell(name,body,depth=1,current="players")


def build_courses(difficulty: list[dict[str,str]], course_records: dict[str,dict[str,str]]) -> str:
    rows=[]
    for d in sorted(difficulty,key=lambda r:inum(r.get('difficulty_rank'),9999)):
        name=d.get('course_name',''); rec=course_records.get(name,{}); holders=' / '.join([x for x in str(rec.get('holder_names','')).split('|') if x]) or '—'; wr=rec.get('course_record_time_ms') or d.get('rank1_time_ms')
        rows.append(f'''<tr class="course-row" data-search="{esc((name+' '+holders).lower())}" data-course="{esc(name.lower())}" data-difficulty="{fnum(d.get('first_place_difficulty'))}" data-outlier="{fnum(d.get('first_place_outlierness'))}" data-competition="{fnum(d.get('top_density'))}" data-wr="{inum(wr)}" data-holder="{esc(holders.lower())}"><td><a class="course-link" href="{esc(course_url(name))}" target="_blank" rel="noopener"><strong>{esc(name)}</strong><span>↗</span></a></td><td class="num strong">{fnum(d.get('first_place_difficulty')):.3f}</td><td class="num">{fnum(d.get('first_place_outlierness')):.3f}</td><td class="num">{fnum(d.get('top_density')):.3f}</td><td class="num strong">{fmt_time(wr)}</td><td>{esc(holders)}</td></tr>''')
    body=f'''<section class="card courses-hero"><p class="eyebrow">Courses</p><h1>Course Rankings</h1><div class="courses-hero-bottom"><div><span>Courses</span><strong>{len(difficulty)}</strong></div><input id="course-search" class="control" type="search" placeholder="Search course or #1 holder"></div></section><section class="card table-card"><div class="table-head"><h2>All Courses</h2></div><div class="table-wrap"><table id="courses-table"><thead><tr><th data-sort="course">Course</th><th data-sort="difficulty">Difficulty</th><th data-sort="outlier">Outlier</th><th data-sort="competition">Competition</th><th data-sort="wr">#1</th><th data-sort="holder">#1 Holder</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>'''
    return shell("Courses",body,current="courses")


def build_about(metadata: dict[str,Any]) -> str:
    theoretical=fnum(metadata.get('catalog_constrained_theoretical_max',{}).get('published_rating'))
    extra='''<script>window.MathJax={tex:{inlineMath:[["\\(","\\)"]],displayMath:[["\\[","\\]"]]},svg:{fontCache:"global"}};</script><script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>'''
    tier_ranges = {
        "Grandmaster":"1300+", "Master I":"1250–1299", "Master II":"1200–1249",
        "Diamond I":"1150–1199", "Diamond II":"1100–1149",
        "Platinum I":"1050–1099", "Platinum II":"1000–1049",
        "Gold I":"950–999", "Gold II":"900–949",
        "Silver I":"850–899", "Silver II":"800–849",
        "Bronze I":"750–799", "Bronze II":"700–749", "Iron":"< 700",
    }
    tiers=''.join(f'<div><span>{esc(t)}</span><strong>{tier_ranges[t]}</strong></div>' for t in TIER_ORDER)
    grades=''.join(f'<div>{grade_badge(g)}<strong>{txt}</strong></div>' for g,txt in [("SS","100.000"),("S+","99.500+"),("S","99.000+"),("A+","98.000+"),("A","96.500+"),("B+","95.000+"),("B","92.500+"),("C+","90.000+"),("C","< 90.000")])
    body=f'''<section class="card about-hero"><div><p class="eyebrow" data-en="About Rating · 1.12.2" data-ja="レート算出方法 · 1.12.2">About Rating · 1.12.2</p><h1 data-en="How Rating Works" data-ja="レートの算出方法">How Rating Works</h1><p data-en="Unofficial rating system for Chelcy 1.12.2." data-ja="Chelcy 1.12.2を対象とした非公式レーティングシステムです。">Unofficial rating system for Chelcy 1.12.2.</p></div><div class="lang-switch"><button class="lang-btn active" data-lang="en">EN</button><button class="lang-btn" data-lang="ja">日本語</button></div></section><section class="method-stack">
{method_card('01','Raw Score','Course performance','コースごとの走力','Your PB is compared with the current #1 time. A score of 100 means the PB equals the #1 time.','自分のPBを現在の1位タイムと比較します。Raw Scoreが100なら、そのコースで1位タイムと同タイムです。','Raw Score = 100 × #1 Time / PB',extra_html=f'<div class="grade-scale">{grades}</div>')}
{method_card('02','Difficulty','0–1 · Updated monthly','0–1 · 月初更新','Difficulty measures how hard it is to produce a time close to #1 — not how difficult the course itself is. It combines #1 outlierness and top-field competition. The table is refreshed at the beginning of each month and then stays fixed.','難易度はコース全体の難しさではなく、「1位に近い記録を出す難しさ」を表す指標です。1位の突出率と上位帯の競争率を組み合わせ、毎月月初に更新して次回更新まで固定します。','Difficulty = 2 × Outlier × Competition / (Outlier + Competition)')}
{method_card('03','Adjusted Score','Difficulty bonus','難易度補正','Difficulty adds up to a 10% bonus to the Raw Score.','難易度に応じてRaw Scoreへ最大10%の補正を加えます。','Multiplier = 1 + 0.10 × Difficulty<br>Adjusted Score = Raw Score × Multiplier')}
{method_card('04','Best 30','Highest adjusted scores','上位30記録を採用','The 30 highest Adjusted Scores are used. Higher positions have more weight.','Adjusted Scoreが高い30コースを採用し、上位ほど大きい重みを付けます。','Performance Index P = Σ(Adjusted Score × Weight) / Σ(Weight)',details='''<details><summary data-en="Exact weight formula" data-ja="重みの正確な式">Exact weight formula</summary><div class="math-block">\\[L(i)=\\frac{{1}}{{1+e^{{0.18(i-20)}}}}\\]\\[w_i=0.50+0.50\\cdot\\frac{{L(i)-L(30)}}{{L(1)-L(30)}}\\]\\[w_1=1.00,\\qquad w_{{30}}=0.50\\]</div></details>''')}
{method_card('05','Rating','Published rating','公開レート','The Performance Index is converted to the displayed Rating with a nonlinear curve.','Performance Indexを非線形の変換式で表示用Ratingに変換します。','P = 100 → Rating 1000',details='''<details><summary data-en="Exact rating formula" data-ja="Ratingの正確な式">Exact rating formula</summary><div class="math-block">\\[h(P)=\\ln\\left(\\frac{{P+50}}{{105-P}}\\right)\\]\\[R(P)=100+900\\cdot\\frac{{h(P)-h(0)}}{{h(100)-h(0)}}\\]\\[x=P-100,\\quad g(x)=\\ln\\left(\\frac{{x+26.25}}{{410-x}}\\right)\\]\\[R(P)=1000+600\\cdot\\frac{{g(x)-g(0)}}{{g(10)-g(0)}}\\]</div></details>''',extra_html=f'<div class="tier-scale">{tiers}</div>')}
<article class="card method-card notes-card"><div class="step-no">+</div><div><div class="method-head"><h2 data-en="Notes" data-ja="補足">Notes</h2></div><div class="notes-list"><div><strong data-en="Difficulty update" data-ja="難易度の更新">Difficulty update</strong><p data-en="The Difficulty table is updated at the beginning of each month and remains fixed until the next update." data-ja="難易度表は毎月月初に更新し、次の月初までは固定します。">The Difficulty table is updated at the beginning of each month and remains fixed until the next update.</p></div><div><strong data-en="Eligible courses" data-ja="対象コース">Eligible courses</strong><p data-en="Limited-time athletics are excluded." data-ja="期間限定アスレチックはレート対象外です。">Limited-time athletics are excluded.</p></div><div><strong data-en="Catalog-constrained theoretical maximum" data-ja="カタログ制約付き理論最大値">Catalog-constrained theoretical maximum</strong><p data-en="With the current catalog and Difficulty table, first place on all 30 highest-Difficulty courses gives a theoretical Rating of {theoretical:.2f}." data-ja="現在の対象コースと難易度表で、難易度上位30コースすべてを1位で走った場合の理論Ratingは {theoretical:.2f} です。">With the current catalog and Difficulty table, first place on all 30 highest-Difficulty courses gives a theoretical Rating of {theoretical:.2f}.</p></div></div></div></article></section>'''
    return shell("About Rating",body,current="about",extra_head=extra)


def method_card(no:str,title:str,note_en:str,note_ja:str,text_en:str,text_ja:str,formula:str,details:str='',extra_html:str='')->str:
    title_ja='難易度' if title=='Difficulty' else title
    return f'''<article class="card method-card"><div class="step-no">{no}</div><div class="method-main"><div class="method-head"><h2 data-en="{esc(title)}" data-ja="{esc(title_ja)}">{esc(title)}</h2><span data-en="{esc(note_en)}" data-ja="{esc(note_ja)}">{esc(note_en)}</span></div><p data-en="{esc(text_en)}" data-ja="{esc(text_ja)}">{esc(text_en)}</p><div class="formula">{formula}</div>{extra_html}{details}</div></article>'''


def simulator_courses(rankings: list[dict[str,str]], difficulty: list[dict[str,str]]) -> dict[str,Any]:
    diff={r.get('course_name',''):r for r in difficulty}; grouped: dict[str,list[int]]=defaultdict(list); cats={}
    for r in rankings:
        c=r.get('course_name','')
        if c not in diff: continue
        t=inum(r.get('time_ms'))
        if t>0: grouped[c].append(t)
        cats[c]=r.get('category','')
    result={}
    for c,d in diff.items():
        times=sorted(grouped.get(c,[]))
        if not times: continue
        result[c]={"name":c,"category":cats.get(c,d.get('category','')),"wr":times[0],"times":times,"difficulty":fnum(d.get('first_place_difficulty')),"multiplier":fnum(d.get('difficulty_multiplier'))}
    return result


def copy_static(root: Path,out: Path)->None:
    dest=out/'static'
    if dest.exists(): shutil.rmtree(dest)
    shutil.copytree(root/'static',dest)


def main()->int:
    a=parse_args()
    paths=[a.overall_csv,a.top40_components_csv,a.player_course_records_csv,a.metadata_json,a.course_difficulty_csv,a.course_records_csv,a.rankings_csv,a.snapshot_summary]
    for p in paths:
        if not p.exists(): raise FileNotFoundError(p)
    overall=read_csv(a.overall_csv); top40=read_csv(a.top40_components_csv); records=read_csv(a.player_course_records_csv); difficulty=read_csv(a.course_difficulty_csv); course_records_rows=read_csv(a.course_records_csv); rankings=read_csv(a.rankings_csv)
    metadata=json.loads(a.metadata_json.read_text(encoding='utf-8'))
    now,data_day=snapshot_info(a.snapshot_summary); diff_day=difficulty_date(metadata,data_day)
    hist_by_player,hist_by_day=load_history(a.history_dir); _,weekly=weekly_reference(now.date(),hist_by_day); events=load_events(a.activity_log)
    top_by_uuid=defaultdict(list); rec_by_uuid=defaultdict(list)
    for r in top40: top_by_uuid[r['player_uuid']].append(r)
    for r in records: rec_by_uuid[r['player_uuid']].append(r)
    cr={r.get('course_name',''):r for r in course_records_rows}
    out=a.out_dir
    if out.exists(): shutil.rmtree(out)
    (out/'players').mkdir(parents=True); (out/'data').mkdir(parents=True)
    copy_static(Path(__file__).resolve().parent,out)
    (out/'index.html').write_text(build_index(overall,hist_by_player,weekly,metadata,data_day,diff_day),encoding='utf-8')
    (out/'courses.html').write_text(build_courses(difficulty,cr),encoding='utf-8')
    (out/'about.html').write_text(build_about(metadata),encoding='utf-8')
    for p in overall:
        u=p['player_uuid']; (out/'players'/f'{u}.html').write_text(build_player(p,top_by_uuid.get(u,[]),rec_by_uuid.get(u,[]),hist_by_player.get(u,[]),events.get(u,[]),now),encoding='utf-8')
    (out/'data'/'simulator_courses.json').write_text(json.dumps(simulator_courses(rankings,difficulty),ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    (out/'.nojekyll').write_text('',encoding='utf-8')
    (out/'site_manifest.json').write_text(json.dumps({"title":SITE_TITLE,"updated":data_day,"difficulty_updated":diff_day,"player_count":len(overall),"course_count":len(difficulty)},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Built {len(overall)} player pages in {out.resolve()}')
    return 0

if __name__=='__main__': raise SystemExit(main())
