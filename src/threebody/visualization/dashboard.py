# ruff: noqa: E501 -- embedded offline HTML/JavaScript is kept readable as a single template.
from __future__ import annotations

import bisect
import html
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np

from threebody.errors import VisualizationError
from threebody.visualization.dashboard_reader import DashboardArchive, load_dashboard_archive
from threebody.visualization.transforms import AU_M, SECONDS_PER_DAY

BODY_IDS = ("star-1", "star-2", "star-3", "planet-1")
BODY_LABELS = ("Star 1", "Star 2", "Star 3", "Planet")
BODY_COLORS = ("#f9c74f", "#f9844a", "#90be6d", "#577590")
ABSOLUTE_MAX_FRAMES = 10_000


def _plotly_javascript(cdn: bool) -> str:
    if cdn:
        return '<script src="https://cdn.plot.ly/plotly-3.3.0.min.js"></script>'
    try:
        from plotly.offline import get_plotlyjs
    except ImportError as exc:
        raise VisualizationError(
            "Plotly is required for dashboard visualization; install with `.[viz]`"
        ) from exc
    return f"<script>{get_plotlyjs()}</script>"


def _safe_json(value) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _select_times(archive: DashboardArchive, frame_cap: int) -> tuple[float, ...]:
    if frame_cap < 2 or frame_cap > ABSOLUTE_MAX_FRAMES:
        raise VisualizationError(f"frame_cap must be within [2, {ABSOLUTE_MAX_FRAMES}]")
    source = [
        point.t_s
        for point in archive.flux
        if archive.t_start_s <= point.t_s <= archive.t_final_s
    ]
    mandatory = {archive.t_start_s, archive.t_final_s}
    for event in archive.events:
        if archive.t_start_s <= event.t_s <= archive.t_final_s:
            mandatory.add(event.t_s)
    if len(mandatory) > frame_cap:
        raise VisualizationError(
            f"frame_cap={frame_cap} cannot retain overlap endpoints and all events"
        )
    if len(source) <= frame_cap:
        return tuple(sorted(set(source) | mandatory))
    selected = set(mandatory)
    for value in np.linspace(0, len(source) - 1, frame_cap):
        selected.add(source[int(round(float(value)))])
        if len(selected) >= frame_cap:
            break
    if len(selected) > frame_cap:
        removable = sorted(selected - mandatory)
        while len(selected) > frame_cap:
            selected.remove(removable.pop())
    return tuple(sorted(selected))


def _linear_index(times: list[float], target: float) -> tuple[int, int, float]:
    right = bisect.bisect_left(times, target)
    if right < len(times) and math.isclose(times[right], target, abs_tol=1.0e-9):
        return right, right, 0.0
    if right == 0 or right == len(times):
        raise VisualizationError("display interpolation would require extrapolation")
    left = right - 1
    fraction = (target - times[left]) / (times[right] - times[left])
    return left, right, fraction


def _interpolate(first, second, fraction: float):
    return (1.0 - fraction) * np.asarray(first) + fraction * np.asarray(second)


def _dashboard_payload(
    archive: DashboardArchive, units: Literal["au", "m"], frame_cap: int
) -> dict[str, object]:
    times = _select_times(archive, frame_cap)
    scale = AU_M if units == "au" else 1.0
    m2_times = [sample.t_s for sample in archive.m2.samples]
    flux_times = [point.t_s for point in archive.flux]
    c0_times = [point.t_s for point in archive.c0]
    c1_times = [point.t_s for point in archive.c1]
    barycentric: dict[str, list[list[float]]] = {body_id: [] for body_id in BODY_IDS}
    planet_relative: dict[str, list[list[float]]] = {body_id: [] for body_id in BODY_IDS}
    flux_series = {body_id: [] for body_id in BODY_IDS[:3]}
    flux_series["total"] = []
    c0_values: list[float | None] = []
    c1_columns: list[list[float]] = []
    latitudes: list[float] = []
    for time in times:
        left, right, fraction = _linear_index(m2_times, time)
        positions: dict[str, np.ndarray] = {}
        for body_id in BODY_IDS:
            first = next(
                body for body in archive.m2.samples[left].bodies if body.id == body_id
            ).position_m
            second = next(
                body for body in archive.m2.samples[right].bodies if body.id == body_id
            ).position_m
            positions[body_id] = (
                np.asarray(first) if left == right else _interpolate(first, second, fraction)
            )
        planet = positions["planet-1"]
        for body_id in BODY_IDS:
            barycentric[body_id].append((positions[body_id] / scale).tolist())
            planet_relative[body_id].append(((positions[body_id] - planet) / scale).tolist())

        f_left, f_right, f_fraction = _linear_index(flux_times, time)
        for star_index, body_id in enumerate(BODY_IDS[:3]):
            first_flux = archive.flux[f_left].stars[star_index].flux_w_m2
            second_flux = archive.flux[f_right].stars[star_index].flux_w_m2
            flux_series[body_id].append(
                first_flux
                if f_left == f_right
                else (1.0 - f_fraction) * first_flux + f_fraction * second_flux
            )
        first_total = archive.flux[f_left].planet_center_flux_w_m2
        second_total = archive.flux[f_right].planet_center_flux_w_m2
        flux_series["total"].append(
            first_total
            if f_left == f_right
            else (1.0 - f_fraction) * first_total + f_fraction * second_total
        )
        if archive.c0:
            t_left, t_right, t_fraction = _linear_index(c0_times, time)
            first_temperature = archive.c0[t_left].temperature_k
            second_temperature = archive.c0[t_right].temperature_k
            c0_values.append(
                first_temperature
                if t_left == t_right
                else (1.0 - t_fraction) * first_temperature
                + t_fraction * second_temperature
            )
        else:
            c0_values.append(None)
        if archive.c1:
            c_left, c_right, c_fraction = _linear_index(c1_times, time)
            first_column = archive.c1[c_left].temperature_k
            second_column = archive.c1[c_right].temperature_k
            column = (
                np.asarray(first_column)
                if c_left == c_right
                else _interpolate(first_column, second_column, c_fraction)
            )
            c1_columns.append(column.tolist())
            if not latitudes:
                latitudes = [
                    math.degrees(value) for value in archive.c1[c_left].latitude_center_rad
                ]
    events = [
        {
            "id": event.event_id,
            "type": event.event_type,
            "t_s": event.t_s,
            "elapsed_days": (event.t_s - archive.t_start_s) / SECONDS_PER_DAY,
            "body_ids": list(event.body_ids),
            "source": event.source,
            "message": event.message,
            "method": event.detection_method,
            "terminal": event.terminal,
        }
        for event in archive.events
    ]
    return {
        "world_id": archive.m2.manifest.world_id,
        "m2_status": archive.m2.manifest.status,
        "times_s": list(times),
        "elapsed_days": [(time - archive.t_start_s) / SECONDS_PER_DAY for time in times],
        "positions": {"barycentric": barycentric, "planet-relative": planet_relative},
        "flux_w_m2": flux_series,
        "c0_temperature_k": c0_values,
        "c1_temperature_k_columns": c1_columns,
        "c1_latitude_deg": latitudes,
        "events": events,
        "m4_classification": archive.m4_classification,
        "units": units,
        "axis_units": "AU" if units == "au" else "m",
        "input_counts": {
            "m2": len(archive.m2.samples),
            "m3": len(archive.flux),
            "frames": len(times),
        },
        "display_semantics": {
            "orbit": "piecewise-linear rendering interpolation; not N-body reintegration",
            "thermal": "piecewise-linear display interpolation; not thermal re-solving",
            "range": "strict M2/M3 overlap without extrapolation",
            "playback": "visual speed only; unrelated to physical simulation cadence",
        },
    }


def _document(payload: dict[str, object], *, cdn: bool) -> str:
    world = html.escape(str(payload["world_id"]))
    plotly = _plotly_javascript(cdn)
    data = _safe_json(payload)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Three Body Planet VIS2 — {world}</title>{plotly}
<style>
body{{margin:0;background:#10141c;color:#e8edf2;font:14px system-ui}}header{{padding:12px 18px}}
.grid{{display:grid;grid-template-columns:3fr 2fr;gap:8px;padding:8px}}.panel{{background:#171e29;border-radius:8px;padding:6px}}
#orbit{{height:620px}}#flux,#c0{{height:260px}}#c1{{height:300px}}.controls{{display:flex;gap:8px;align-items:center;padding:12px;position:sticky;bottom:0;background:#10141cee}}
#time-slider{{flex:1}}.notice{{color:#f4c36a}}@media(max-width:900px){{.grid{{display:block}}#orbit{{height:430px}}}}
</style></head><body>
<header><h2>Linked orbit · irradiation · temperature — {world}</h2>
<div class="notice" id="status"></div><div id="semantics"></div><div id="fate"></div></header>
<div class="grid"><div class="panel"><div id="orbit"></div></div><div>
<div class="panel"><div id="flux"></div></div><div class="panel"><div id="c0"></div></div>
<div class="panel"><div id="c1"></div></div><div class="panel"><label>Event jump <select id="event-select"><option value="">Select event</option></select></label><div id="event-detail"></div></div>
</div></div>
<div class="controls"><button id="play">Play</button><button id="pause">Pause</button>
<label>Speed <select id="speed"><option value="1">1×</option><option value="4">4×</option><option value="16">16×</option></select></label>
<label>Camera <select id="center"><option value="barycentric">Barycentric</option><option value="planet-relative">Planet relative</option></select></label>
<input id="time-slider" type="range" min="0" max="0" value="0"><span id="time-label"></span></div>
<script id="dashboard-data" type="application/json">{data}</script>
<script>
"use strict";
const D=JSON.parse(document.getElementById("dashboard-data").textContent);let index=0,timer=null;
const ids=["star-1","star-2","star-3","planet-1"],labels=["Star 1","Star 2","Star 3","Planet"],colors=["#f9c74f","#f9844a","#90be6d","#577590"];
const slider=document.getElementById("time-slider");slider.max=D.times_s.length-1;
function orbitTraces(center,upto){{return ids.map((id,i)=>{{const p=D.positions[center][id].slice(0,upto+1);return {{type:"scatter3d",mode:"lines+markers",name:labels[i],x:p.map(v=>v[0]),y:p.map(v=>v[1]),z:p.map(v=>v[2]),line:{{color:colors[i]}},marker:{{size:i===3?5:7,color:colors[i]}}}};}});}}
const baseLayout={{paper_bgcolor:"#171e29",plot_bgcolor:"#171e29",font:{{color:"#e8edf2"}},margin:{{l:45,r:15,t:40,b:40}}}};
Plotly.newPlot("orbit",orbitTraces("barycentric",0),{{...baseLayout,title:"Four-body trajectory",scene:{{aspectmode:"data",xaxis:{{title:`x (${{D.axis_units}})`}},yaxis:{{title:`y (${{D.axis_units}})`}},zaxis:{{title:`z (${{D.axis_units}})`}}}}}},{{responsive:true,displaylogo:false}});
const days=D.elapsed_days;const fluxTraces=ids.slice(0,3).map((id,i)=>({{x:days,y:D.flux_w_m2[id],name:labels[i],line:{{color:colors[i]}}}}));fluxTraces.push({{x:days,y:D.flux_w_m2.total,name:"Total",line:{{color:"#ffffff",width:3}}}});
Plotly.newPlot("flux",fluxTraces,{{...baseLayout,title:"Stellar irradiance at planet center",xaxis:{{title:"elapsed days"}},yaxis:{{title:"W/m²"}}}},{{responsive:true,displaylogo:false}});
const c0ok=D.c0_temperature_k.some(v=>v!==null);Plotly.newPlot("c0",c0ok?[{{x:days,y:D.c0_temperature_k,name:"C0",line:{{color:"#e76f51"}}}}]:[],{{...baseLayout,title:c0ok?"C0 global temperature":"C0 unavailable",xaxis:{{title:"elapsed days"}},yaxis:{{title:"K"}}}},{{responsive:true,displaylogo:false}});
const c1z=D.c1_latitude_deg.map((_,band)=>D.c1_temperature_k_columns.map(col=>col[band]));Plotly.newPlot("c1",c1z.length?[{{type:"heatmap",x:days,y:D.c1_latitude_deg,z:c1z,colorscale:"Turbo",colorbar:{{title:"K"}}}}]:[],{{...baseLayout,title:c1z.length?"C1 latitude temperature":"C1 unavailable",xaxis:{{title:"elapsed days"}},yaxis:{{title:"latitude (deg)"}}}},{{responsive:true,displaylogo:false}});
function cursorLayout(day){{return {{shapes:[{{type:"line",x0:day,x1:day,y0:0,y1:1,yref:"paper",line:{{color:"#ffffff",dash:"dot"}}}}]}};}}
function updateDashboard(next){{index=Math.max(0,Math.min(D.times_s.length-1,Number(next)));slider.value=index;const center=document.getElementById("center").value;Plotly.react("orbit",orbitTraces(center,index),document.getElementById("orbit").layout);Plotly.relayout("flux",cursorLayout(days[index]));Plotly.relayout("c0",cursorLayout(days[index]));Plotly.relayout("c1",cursorLayout(days[index]));document.getElementById("time-label").textContent=`t=${{D.times_s[index].toPrecision(7)}} s / ${{days[index].toPrecision(7)}} d`;}}
slider.addEventListener("input",e=>updateDashboard(e.target.value));document.getElementById("center").addEventListener("change",()=>updateDashboard(index));
document.getElementById("play").onclick=()=>{{clearInterval(timer);timer=setInterval(()=>{{if(index>=D.times_s.length-1){{clearInterval(timer);return;}}updateDashboard(index+1);}},Math.max(20,200/Number(document.getElementById("speed").value)));}};document.getElementById("pause").onclick=()=>clearInterval(timer);
const eventSelect=document.getElementById("event-select");D.events.forEach((event,i)=>{{const option=document.createElement("option");option.value=i;option.textContent=`${{event.source}} · ${{event.type}} · ${{event.elapsed_days.toPrecision(5)}} d`;eventSelect.appendChild(option);}});eventSelect.onchange=()=>{{if(eventSelect.value==="")return;const event=D.events[Number(eventSelect.value)];let nearest=0,best=Infinity;D.times_s.forEach((time,i)=>{{const delta=Math.abs(time-event.t_s);if(delta<best){{best=delta;nearest=i;}}}});updateDashboard(nearest);document.getElementById("event-detail").textContent=`${{event.type}} [${{event.body_ids.join(", ")}}] — ${{event.message}}; method=${{event.method}}${{event.terminal?"; terminal scientific timeline":""}}`;}};
document.getElementById("status").textContent=`M2 status=${{D.m2_status}} · frames=${{D.input_counts.frames}} · no extrapolation beyond M2/M3 overlap`;
document.getElementById("semantics").textContent=Object.values(D.display_semantics).join(" · ");document.getElementById("fate").textContent=D.m4_classification?`M4: ${{JSON.stringify(D.m4_classification)}}`:"M4 outcome archive not supplied (optional)";updateDashboard(0);
</script></body></html>"""


def export_dashboard_html(
    m2_dir: str | Path,
    m3_dir: str | Path,
    output_path: str | Path,
    *,
    m4_dir: str | Path | None = None,
    units: Literal["au", "m"] = "au",
    frame_cap: int = 1000,
    max_input_samples: int = 100_000,
    cdn: bool = False,
    overwrite: bool = False,
) -> dict[str, object]:
    output = Path(output_path)
    if output.suffix.lower() != ".html":
        raise VisualizationError("dashboard output path must end with .html")
    if output.exists() and not overwrite:
        raise VisualizationError(
            f"output already exists: {output}; pass --overwrite to replace it"
        )
    archive = load_dashboard_archive(
        m2_dir, m3_dir, m4_dir, max_input_samples=max_input_samples
    )
    payload = _dashboard_payload(archive, units, frame_cap)
    document = _document(payload, cdn=cdn)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return {
        "output": str(output),
        "world_id": archive.m2.manifest.world_id,
        "m2_status": archive.m2.manifest.status,
        "m2_samples": len(archive.m2.samples),
        "m3_samples": len(archive.flux),
        "displayed_frames": len(payload["times_s"]),
        "events": len(archive.events),
        "m4_loaded": archive.m4_manifest is not None,
        "units": units,
        "plotly_js": "cdn" if cdn else "embedded",
        "file_size_bytes": output.stat().st_size,
    }
