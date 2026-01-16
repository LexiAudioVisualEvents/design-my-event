import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import "./styles.css";

const MOODS = ["Editorial", "Luxe", "Minimal", "Mediterranean", "Manhattan"];
const PALETTES = ["Terracotta", "Champagne", "Slate", "Coastal Neutral"];
const LAYOUTS = ["Cocktail", "Long Tables", "Banquet", "Theatre"];

const API_BASE = import.meta.env.VITE_API_URL;

const VENUES = {
  "venue-1": {
    label: "Venue 1",
    heroUrl: "/venues/venue-1/hero.JPG",
    referenceUrl: `${window.location.origin}/venues/venue-1/hero.JPG`,
  },
  "venue-2": {
    label: "Venue 2",
    heroUrl: "/venues/venue-2/hero.JPG",
    referenceUrl: `${window.location.origin}/venues/venue-2/hero.JPG`,
  },
};

function TileGroup({ title, items, value, onChange, subtitle }) {
  return (
    <section className="card">
      <div className="cardHeader">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p className="muted">{subtitle}</p> : null}
        </div>
        <span className="pill">{value ? "Selected" : "Pick one"}</span>
      </div>

      <div className="grid">
        {items.map((item) => {
          const selected = value === item;
          return (
            <button
              key={item}
              className={`tile ${selected ? "selected" : ""}`}
              onClick={() => onChange(item)}
              type="button"
            >
              <div className="tileTitle">{item}</div>
              <div className="tileHint">{selected ? "✓ Locked in" : "Select"}</div>
            </button>
          );
        })}
      </div>
    </section>
  );
}

export default function App() {
  const [mood, setMood] = useState("");
  const [palette, setPalette] = useState("");
  const [layout, setLayout] = useState("");
  const [room, setRoom] = useState("");
  
  const [venueId, setVenueId] = useState("venue-1");
  const venue = VENUES[venueId];

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null); // { image_data_url, prompt, cache_hit }
  const [error, setError] = useState("");
  const [history, setHistory] = useState([]); // array of { mood,palette,layout, image_data_url }

  const ready = useMemo(() => mood && palette && layout, [mood, palette, layout]);
  
  const pickVenueImage = () => venue.referenceUrl;

  async function generate() {
    setError("");
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
		mood,
		palette,
		layout,
		room: room.trim() || null,
		venue_image_url: pickVenueImage(),
}),
      });

      if (!res.ok) {
        const msg = await res.text();
        throw new Error(msg || `HTTP ${res.status}`);
      }

      const data = await res.json();
      setResult(data);

      setHistory((prev) => {
        const next = [
          { mood, palette, layout, image_data_url: data.image_data_url },
          ...prev,
        ];
        return next.slice(0, 6);
      });
    } catch (e) {
      setError(e?.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  function loadFromHistory(h) {
    setMood(h.mood);
    setPalette(h.palette);
    setLayout(h.layout);
    setResult({ image_data_url: h.image_data_url, prompt: "", cache_hit: true });
    setError("");
  }

  return (
    <div className="page">
      <header className="topbar">
  <div>
    <div className="brand">Design My Event</div>
    <div className="muted">
      Pick a Mood, Palette, and Layout — generate a venue-ready moodboard.
    </div>
  </div>

  <div className="topControls">
	<div className="roomWrap">
      <label className="roomLabel">Room (optional)</label>
      <input
        className="roomInput"
        value={room}
        onChange={(e) => setRoom(e.target.value)}
        placeholder="e.g. Ballroom A / Terrace / Foyer"
      />
    </div>
  </div>
</header>

      <main className="layout">
        <div className="left">
			<TileGroup
			title="Venue"
			subtitle="Choose the reference venue"
	>
			<div className="venueButtons">
				{Object.entries(VENUES).map(([id, v]) => {
					const selected = id === venueId;
					return (
						<button
							key={id}
							type="button"
							className={`tile ${selected ? "selected" : ""}`}
							onClick={() => setVenueId(id)}
        >
							{v.label}
							</button>
					);
				})}
			</div>
			</TileGroup>
		  
		  
		  
		  <TileGroup
            title="Mood"
            subtitle="Overall styling language"
            items={MOODS}
            value={mood}
            onChange={setMood}
          />
          <TileGroup
            title="Palette"
            subtitle="Color + material cues"
            items={PALETTES}
            value={palette}
            onChange={setPalette}
          />
          <TileGroup
            title="Layout"
            subtitle="How guests experience the room"
            items={LAYOUTS}
            value={layout}
            onChange={setLayout}
          />

          <div className="ctaRow">
            <button className="primary" disabled={!ready || loading} onClick={generate}>
              {loading ? "Designing…" : "Generate Moodboard"}
            </button>
            <button
              className="secondary"
              disabled={loading || !result}
              onClick={generate}
              type="button"
              title="Generate again with the same selections"
            >
              Regenerate
            </button>
          </div>

          {error ? <div className="error">{error}</div> : null}

          {history.length ? (
            <section className="card">
              <div className="cardHeader">
                <h2>Recent concepts</h2>
                <span className="muted">{history.length} / 6</span>
              </div>
              <div className="history">
                {history.map((h, idx) => (
                  <button key={idx} className="historyItem" onClick={() => loadFromHistory(h)}>
                    <img src={h.image_data_url} alt="history" />
                    <div className="historyMeta">
                      <div className="historyLine">{h.mood}</div>
                      <div className="historyLine muted">{h.palette} • {h.layout}</div>
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ) : null}
        </div>

        <div className="right">
          <section className="previewCard">
            <div className="previewHeader">
              <h2>Preview</h2>
              <div className="muted">
                {result?.cache_hit ? "Instant (cached)" : loading ? "Generating" : result ? "Ready" : "Waiting"}
              </div>
            </div>

            <div className="previewBody">
              <AnimatePresence mode="wait">
                {loading ? (
                  <motion.div
                    key="loading"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="shimmer"
                  >
                    <div className="shimmerBox" />
                    <div className="shimmerText" />
                    <div className="shimmerText small" />
                  </motion.div>
                ) : result?.image_data_url ? (
                  <motion.div
                    key="img"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="imgWrap"
                  >
                    <img className="previewImg" src={result.image_data_url} alt="Generated moodboard" />
                    <div className="caption">
                      <div className="capStrong">{mood} • {palette} • {layout}</div>
                      {room ? <div className="muted">Room: {room}</div> : null}
                    </div>
                  </motion.div>
                ) : (
                  <motion.div
                    key="empty"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="empty"
                  >
                    <div className="emptyTitle">Make your picks →</div>
                    <div className="muted">Then hit “Generate Moodboard”.</div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </section>
        </div>
      </main>

      <footer className="footer muted">
        Tip: put this on a kiosk iPad, full-screen Chrome/Edge. Keep a “Room” preset per space if you want it to feel venue-specific.
      </footer>
    </div>
  );
}
