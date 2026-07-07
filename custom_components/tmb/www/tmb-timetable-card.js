/**
 * tmb-timetable-card
 *
 * A live departure board for one TMB (bus or metro) station, built from
 * this integration's per-line sensors (entities carrying a `stop_name`
 * attribute matching the configured station). No dependencies — a plain
 * custom element. Add one card per station; switch stations by changing
 * the `station` value, so the same card type covers every stop you've
 * configured.
 *
 * Card config:
 *   type: custom:tmb-timetable-card
 *   station: Passeig de Gràcia   # must match a sensor's stop_name attribute
 *   title: My Station             # optional, defaults to the station name
 *   rows: 6                       # optional, default 6
 */

const BG = "#1a1a1a";
const WHITE = "#ffffff";
const DIM = "rgba(255,255,255,0.6)";
const DIVIDER = "rgba(255,255,255,0.12)";
const ACCENT = "#da291c"; // TMB red

function _textColorFor(hexColor) {
  const hex = (hexColor || "").replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(hex)) return WHITE;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.6 ? "#000000" : "#ffffff";
}

class TmbTimetableCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.station) {
      throw new Error(
        "tmb-timetable-card: you must set 'station' (a sensor's stop_name attribute) in the card config."
      );
    }
    this._config = { rows: 6, ...config };
    this._built = false;
  }

  getCardSize() {
    return 1 + (this._config ? this._config.rows : 6);
  }

  static getStubConfig() {
    return { station: "", rows: 6 };
  }

  connectedCallback() {
    if (!this._clockTimer) {
      this._clockTimer = setInterval(() => this._tick(), 1000);
    }
  }

  disconnectedCallback() {
    if (this._clockTimer) {
      clearInterval(this._clockTimer);
      this._clockTimer = null;
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._build();
      this._built = true;
    }
    this._renderRows();
  }

  _build() {
    const card = document.createElement("ha-card");
    card.classList.add("tmb-timetable-card");

    const style = document.createElement("style");
    style.textContent = `
      ha-card.tmb-timetable-card {
        background: ${BG};
        overflow: hidden;
        padding: 0;
      }
      .tmb-header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 14px 16px 10px;
        border-bottom: 3px solid ${ACCENT};
      }
      .tmb-title-block {
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .tmb-station-name {
        color: ${WHITE};
        font-size: 0.95em;
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .tmb-clock {
        color: ${DIM};
        font-size: 0.8em;
        font-weight: 500;
        letter-spacing: 0.02em;
      }
      .tmb-spacer { flex: 1; }
      .tmb-row {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 10px 16px;
        border-top: 1px solid ${DIVIDER};
      }
      .tmb-pill {
        flex-shrink: 0;
        min-width: 2.6em;
        box-sizing: border-box;
        text-align: center;
        border-radius: 6px;
        padding: 0.3em 0.6em;
        font-weight: 700;
        font-size: 1.0em;
      }
      .tmb-mode-icon {
        flex-shrink: 0;
        --mdc-icon-size: 18px;
        color: ${DIM};
      }
      .tmb-dest {
        flex: 1;
        min-width: 0;
        color: ${WHITE};
        font-size: 1.1em;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .tmb-mins {
        flex-shrink: 0;
        color: ${WHITE};
        font-size: 1.1em;
        font-weight: 600;
        min-width: 3.6em;
        text-align: right;
      }
      .tmb-empty {
        padding: 20px 16px 24px;
        color: ${WHITE};
        opacity: 0.6;
        font-size: 1.0em;
      }
    `;

    const wrapper = document.createElement("div");
    wrapper.innerHTML = `
      <div class="tmb-header">
        <div class="tmb-title-block">
          <div class="tmb-station-name"></div>
          <div class="tmb-clock">--:--</div>
        </div>
        <div class="tmb-spacer"></div>
      </div>
      <div class="tmb-rows"></div>
    `;

    card.appendChild(style);
    card.appendChild(wrapper);
    this.innerHTML = "";
    this.appendChild(card);

    this._clockEl = wrapper.querySelector(".tmb-clock");
    this._rowsEl = wrapper.querySelector(".tmb-rows");
    wrapper.querySelector(".tmb-station-name").textContent =
      this._config.title || this._config.station;

    this._tick();
  }

  _tick() {
    if (this._clockEl) {
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, "0");
      const mm = String(now.getMinutes()).padStart(2, "0");
      this._clockEl.textContent = `${hh}:${mm}`;
    }
  }

  /**
   * Unlike an absolute-timestamp schedule, this integration's sensors
   * already report "minutes from now" as of their last poll (~30s), so
   * there's nothing to interpolate client-side — just read the current
   * state/attributes on every hass update.
   */
  _collectRows() {
    if (!this._hass) return [];
    const station = this._config.station;
    const entities = Object.values(this._hass.states).filter(
      (e) =>
        e.entity_id.startsWith("sensor.") &&
        e.attributes &&
        e.attributes.stop_name === station
    );
    this._noEntitiesFound = entities.length === 0;

    const rows = [];
    for (const entity of entities) {
      const a = entity.attributes;
      const mins = Number(entity.state);
      if (Number.isFinite(mins)) {
        rows.push({
          line: a.line,
          line_color: a.line_color,
          mode: a.mode,
          destination: a.destination,
          mins,
        });
      }
      if (Array.isArray(a.upcoming)) {
        for (const next of a.upcoming) {
          if (Number.isFinite(next.next_arrival)) {
            rows.push({
              line: a.line,
              line_color: a.line_color,
              mode: a.mode,
              destination: next.destination,
              mins: next.next_arrival,
            });
          }
        }
      }
    }
    rows.sort((x, y) => x.mins - y.mins);
    return rows.slice(0, this._config.rows);
  }

  _renderRows() {
    if (!this._rowsEl || !this._hass) return;
    this._rowsEl.innerHTML = "";

    const rows = this._collectRows();

    if (rows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "tmb-empty";
      empty.textContent = this._noEntitiesFound
        ? `No TMB sensors found for station "${this._config.station}" — check the name matches exactly (see a sensor's stop_name attribute).`
        : "No arrivals forecast right now";
      this._rowsEl.appendChild(empty);
      return;
    }

    for (const row of rows) {
      const rowEl = document.createElement("div");
      rowEl.className = "tmb-row";

      const icon = document.createElement("ha-icon");
      icon.className = "tmb-mode-icon";
      icon.setAttribute(
        "icon",
        row.mode === "metro" ? "mdi:subway-variant" : "mdi:bus"
      );

      const pill = document.createElement("div");
      pill.className = "tmb-pill";
      pill.style.background = row.line_color ? `#${row.line_color}` : "#666";
      pill.style.color = _textColorFor(row.line_color);
      pill.textContent = row.line || "";

      const dest = document.createElement("div");
      dest.className = "tmb-dest";
      dest.textContent = row.destination || "";

      const minsEl = document.createElement("div");
      minsEl.className = "tmb-mins";
      minsEl.textContent = `${row.mins} min`;

      rowEl.append(icon, pill, dest, minsEl);
      this._rowsEl.appendChild(rowEl);
    }
  }
}

customElements.define("tmb-timetable-card", TmbTimetableCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "tmb-timetable-card",
  name: "TMB Timetable",
  description: "Live bus/metro departure board for one TMB station.",
});
