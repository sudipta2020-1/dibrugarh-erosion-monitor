# Dibrugarh Soil Erosion Monitoring Dashboard

An automated system that captures satellite imagery of Dibrugarh district every week, maps soil erosion risk and land change, and publishes the results to a web dashboard that is embedded in a Google Site.

## 1. How the system works

```
GitHub Actions (weekly schedule)
   └─ pipeline/run.py
        1. Downloads the district boundary (geoBoundaries)
        2. Captures Sentinel-2, Sentinel-1 and Copernicus DEM data (Planetary Computer)
        3. Computes indices, water extent, RUSLE soil loss and change since the baseline
        4. Ranks priority sites for field verification
        5. Writes web layers and tables to docs/data/
   ├─ commits docs/data/  ──►  GitHub Pages hosts the dashboard (docs/index.html)
   └─ creates a GitHub release with the full-resolution GeoTIFFs and GeoJSON
                                   │
Google Site  ◄── Embed (By URL) ───┘
```

The Google Site does not run any code. It shows the GitHub Pages dashboard inside an embed block, so every weekly run updates the Google Site on its own.

**Monitoring windows.** Each run compares the latest 120 days with the same calendar dates in the baseline year (2020 by default). Comparing the same months limits false change from seasonal river levels and crop cycles. During the monsoon, cloud cover reduces the optical view; such runs are still published but marked as low confidence on the dashboard, and water change is taken from radar.

## 2. One-time setup (about 15 minutes)

You need a free GitHub account and the Google account that owns the Google Site.

**Step 1. Create the repository**

1. Sign in to GitHub and create a new **public** repository, for example `dibrugarh-erosion-monitor`. Public repositories get free GitHub Pages hosting and larger free Actions runners (4 CPU, 16 GB RAM).
2. Upload all files from this folder, keeping the folder structure (`pipeline/`, `docs/`, `.github/workflows/`). The easiest way is *Add file → Upload files* and dragging the whole folder in. Make sure the hidden `.github` folder is included; if your file browser hides it, upload `monitor.yml` separately into `.github/workflows/` using *Add file → Create new file*.

**Step 2. Allow the workflow to write results**

*Settings → Actions → General → Workflow permissions →* select **Read and write permissions** → *Save*.

**Step 3. Turn on GitHub Pages**

*Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main`, folder `/docs`* → *Save*. After a minute the page address appears, for example `https://<your-username>.github.io/dibrugarh-erosion-monitor/`.

**Step 4. Run the first monitoring job**

*Actions → Erosion monitoring run → Run workflow*. Leave the date blank to use today, or enter an end date such as `2026-02-28` to start with a dry-season window. The first run takes about 30 to 90 minutes. When it finishes, the dashboard fills in.

From then on the job runs by itself at 08:40 IST every Monday. Sentinel-2 passes over Assam about every 5 days, so each weekly run has new images; in the monsoon, cloudy weeks rely more on radar and are marked as low confidence. It can also be started by hand at any time from the Actions tab.

**Step 5. Embed the dashboard in Google Sites**

1. Open your site in Google Sites and go to the page where the dashboard should appear.
2. In the right panel choose *Insert → Embed → By URL*.
3. Paste the GitHub Pages address from Step 3 and choose **Whole page**.
4. Drag the corner of the embed block to make it full width and about 1,600 px tall, so the map, charts and table are visible without inner scrolling.
5. Click *Publish*.

You can add a second page to the site with the downloads (Insert → Button, linking to `…/data/hotspots.csv` or to the repository's *Releases* page).

## 3. Repository contents

| Path | Purpose |
|---|---|
| `config.yaml` | Study area, grid, monitoring windows, RUSLE inputs and thresholds |
| `pipeline/acquire.py` | Satellite data search, cloud masking and compositing |
| `pipeline/analysis.py` | Indices, water mask, RUSLE, change detection, hotspot ranking |
| `pipeline/publish.py` | Web map layers, summary, history and release files |
| `pipeline/run.py` | Entry point used by the scheduled job |
| `.github/workflows/monitor.yml` | Monthly schedule and publishing steps |
| `docs/index.html`, `docs/assets/` | Dashboard page |
| `docs/data/` | Results written by each run (do not edit by hand) |

## 4. Changing the system

- **Schedule.** Edit the `cron` line in `.github/workflows/monitor.yml`. For example `10 3 1,15 * *` runs on the 1st and 15th. Times are in UTC.
- **Window length or baseline year.** Edit `monitoring` in `config.yaml`.
- **Rainfall and soil data.** Add a rainfall raster (IMD or CHIRPS, annual mm) and a K-factor raster to the repository and set `rainfall_raster` and `k_raster` in `config.yaml`. Until then soil loss values show relative risk only.
- **Department boundary.** Add the boundary GeoJSON to the repository and set `aoi_geojson`.
- **Running locally.** `pip install -r requirements.txt` then `python pipeline/run.py --skip-publish`.

## 4a. Accuracy assessment

The first monitoring run with this feature draws a stratified random sample of
about 300 points (bank erosion of stable land, char loss, accretion, stable land,
stable water) and publishes it with image chips in `docs/data/validation/`.

1. Open `validate.html` on the dashboard site and label every point. The map
   class is hidden, so the labels stay independent of the map.
2. Click **Download labels (CSV)** and upload the file to the repository as
   `data/validation_labels.csv` (Add file, Upload files).
3. Run the "Erosion monitoring run" workflow with the end date shown on the
   dashboard's accuracy card (the end date of the sampled window).

The run then reports overall, user's and producer's accuracy and error-corrected
areas with 95% confidence intervals (Olofsson et al., 2014; Stehman, 2014). The
same labels can be used to compare later versions of the method on that window.
To draw a new sample, delete `docs/data/validation/`.

## 4b. Field verification and recommended measures

Every run writes a mobile survey form and the month's priority sites to
`docs/data/field/` (also linked on the dashboard under "Field verification"):

- `erosion_field_survey.xlsx`: the form in XLSForm format. It works offline on
  Android with KoboCollect or ODK Collect, and in any phone browser through the
  KoboToolbox web form. It records GPS, erosion type and severity, what happened
  since the baseline year, assets and households at risk, existing and
  recommended measures, urgency, and up to three photographs.
- `priority_sites.csv`: the site list the form reads (site ID, location, type,
  urgency and suggested action).

Setup in KoboToolbox (free account at kf.kobotoolbox.org, or the humanitarian
server at eu.kobotoolbox.org):
1. New project, "Upload an XLSForm", choose `erosion_field_survey.xlsx`.
2. Settings, Media: upload `priority_sites.csv` (keep this exact file name). Deploy.
3. Share the form with field staff (KoboCollect app or the web link).
4. Each month, replace the media file with the new `priority_sites.csv`.
5. Download the data as CSV and upload it to the repository as
   `data/field_records.csv`. The next run matches each record to a site (by its
   site ID, or by GPS within 300 m), marks the site as confirmed or not, and
   reports the confirmation rate (the share of visited sites where erosion was
   found), false alarms and new sites on the dashboard.

Each priority site also gets an urgency (Immediate, Before monsoon, Routine,
Monitor), a lead agency and a recommended measure, from the rules in
`pipeline/interventions.py`. The thresholds are in `config.yaml` under
`interventions`. The rules are a first screening; the field visit decides.

## 4c. IoT sensor stations (pilot)

The dashboard has a "Live sensor stations" panel for stations at the very high
risk (immediate action) and high risk (before monsoon) bank erosion sites. Until hardware is installed it shows
clearly labelled simulated readings, so the panel and the alert rules can be
demonstrated. Stations and thresholds are set in `docs/data/sensors/stations.json`.

**Each station**
- River level: ultrasonic or radar level sensor on a bridge or pole.
- Rain gauge: tipping bucket.
- Soil moisture at 30, 60 and 100 cm in the bank.
- Piezometer: a vibrating-wire sensor in a borehole about 3 m deep and 10 m behind
  the edge. It measures the water pressure inside the bank. When the river falls
  faster than the bank drains, this pressure stays high and the bank is weakest.
- Tilt nodes: MEMS accelerometer nodes on stakes 5, 10, 20 and 40 m back from the
  bank edge. A node that tilts and then stops reporting usually means the edge has
  reached it; these nodes are expendable.
- LoRa radios on the nodes, and one solar-powered gateway with a 4G (or NB-IoT)
  link, mounted above the highest flood level.

**Connecting real stations (ThingSpeak)**
1. Create two ThingSpeak channels per station: a main channel with
   field1 = river level (m), field2 = rain (mm in the interval), field3-field5 =
   soil moisture (%) at 30, 60 and 100 cm, field6 = battery (V), field7 = pore-water pressure (kPa); and a tilt
   channel with field1-field4 = tilt (degrees) of the nodes at 5, 10, 20 and 40 m.
2. Have the gateway post readings every 15 minutes (ThingSpeak HTTP or MQTT API).
3. In `stations.json`, set `"mode": "thingspeak"` and fill in each station's
   channel IDs and read API keys (leave the keys empty for public channels).

The panel refreshes every minute and evaluates these rules: river level falling
faster than 0.5 m in 6 h (rapid drawdown, when banks most often fail), rising
faster than 1 m in 6 h, above the station's warning level, rain of 64.5 mm or
more in 24 h (IMD "heavy rain"), bank soil at or above 45% moisture, a tilt
increase of 3 degrees or more in 24 h, a tilt of 10 degrees or more, a tilted node
that stops reporting (possible bank failure), and low battery. SMS or WhatsApp
alerts need a small server-side job and are a later step.

## 5. Notes and limits

- GitHub disables scheduled workflows in repositories with no activity for 60 days. Each run commits new results, which counts as activity, so the schedule stays active as long as the runs succeed.
- If a run fails (for example a temporary outage of the satellite catalogue), GitHub emails the repository owner. The dashboard keeps showing the last successful run. Start the workflow again by hand.
- The dashboard map layers are reduced to about 1,800 pixels across for fast loading. Full-resolution GeoTIFFs for GIS work are attached to each release.
- All results, especially vegetation loss and new bare soil, should be confirmed in the field before conservation works are planned. Tea garden pruning and paddy harvest can look like vegetation loss.
