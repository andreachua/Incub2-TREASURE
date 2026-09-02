<script type="text/x-dc" data-dc-script="" data-props="{&quot;startScreen&quot;:{&quot;editor&quot;:&quot;enum&quot;,&quot;options&quot;:[&quot;home&quot;,&quot;form&quot;,&quot;assets&quot;],&quot;default&quot;:&quot;home&quot;,&quot;tsType&quot;:&quot;\&quot;home\&quot; | \&quot;form\&quot; | \&quot;assets\&quot;&quot;},&quot;showSourceRail&quot;:{&quot;editor&quot;:&quot;boolean&quot;,&quot;default&quot;:true,&quot;tsType&quot;:&quot;boolean&quot;},&quot;showProvenanceLegend&quot;:{&quot;editor&quot;:&quot;boolean&quot;,&quot;default&quot;:true,&quot;tsType&quot;:&quot;boolean&quot;}}">
class Component extends DCLogic {
  constructor(props) {
    super(props);
    this.state = { screen: props.startScreen || "home", toast: false, err: false };
    this.tipEl = null;
  }

  go(screen) {
    this.setState({ screen, err: false }, () => { if (this._syncCount) this._syncCount(); });
    window.scrollTo(0, 0);
  }

  submit() {
    const sel = document.querySelector('[data-manual-select]');
    if (sel && !sel.value) {
      this.setState({ err: true });
      sel.style.borderColor = "var(--prizm-color-danger)";
      sel.focus();
      return;
    }
    this.setState({ screen: "assets", toast: true, err: false, registered: true });
    window.scrollTo(0, 0);
    clearTimeout(this._t);
    this._t = setTimeout(() => this.setState({ toast: false }), 7000);
  }

  componentDidMount() {
    const root = document.body;

    // keep the description counter honest whatever the seeded copy is
    const syncCount = () => {
      const ta = document.querySelector("[data-desc]");
      const c = document.querySelector("[data-desc-count]");
      if (ta && c) c.textContent = (800 - ta.value.length) + " characters remaining";
    };
    syncCount();
    this._syncCount = syncCount;

    // floating tooltip for (i) info icons
    const tip = document.createElement("div");
    Object.assign(tip.style, {
      position: "fixed", zIndex: 90, maxWidth: "260px", padding: "8px 10px",
      borderRadius: "6px", background: "var(--prizm-color-fg)", color: "var(--prizm-color-bg)",
      font: "400 12px/1.45 var(--prizm-font-sans)", boxShadow: "var(--prizm-shadow-lg)",
      opacity: "0", pointerEvents: "none", transition: "opacity 150ms ease-out"
    });
    document.body.appendChild(tip);
    this.tipEl = tip;

    root.addEventListener("mouseover", (e) => {
      const t = e.target.closest("[data-tip]");
      if (!t) return;
      tip.textContent = t.getAttribute("data-tip");
      const r = t.getBoundingClientRect();
      tip.style.opacity = "1";
      const tr = tip.getBoundingClientRect();
      tip.style.left = Math.max(12, Math.min(window.innerWidth - tr.width - 12, r.left - tr.width / 2 + r.width / 2)) + "px";
      tip.style.top = (r.top - tr.height - 8 < 8 ? r.bottom + 8 : r.top - tr.height - 8) + "px";
    });
    root.addEventListener("mouseout", (e) => {
      if (e.target.closest("[data-tip]")) tip.style.opacity = "0";
    });

    // AI reasoning popover — hover to view
    root.addEventListener("mouseover", (e) => {
      const trigger = e.target.closest("[data-ai-trigger]");
      const pop = document.querySelector("[data-ai-pop]");
      if (!pop) return;
      if (trigger || e.target.closest("[data-ai-pop]")) {
        pop.style.opacity = "1";
        pop.style.pointerEvents = "auto";
        pop.style.transform = "translateY(0)";
      }
    });
    root.addEventListener("mouseout", (e) => {
      const pop = document.querySelector("[data-ai-pop]");
      if (!pop) return;
      const to = e.relatedTarget;
      if (to && (to.closest && (to.closest("[data-ai-trigger]") || to.closest("[data-ai-pop]")))) return;
      if (e.target.closest("[data-ai-trigger]") || e.target.closest("[data-ai-pop]")) {
        pop.style.opacity = "0";
        pop.style.pointerEvents = "none";
        pop.style.transform = "translateY(3px)";
      }
    });

    // mark extracted fields as edited once the operator changes them
    root.addEventListener("input", (e) => {
      const f = e.target.closest('[data-prov="ai"]');
      if (f && !f.querySelector('[data-chip="edited"]')) {
        const chip = document.createElement("span");
        chip.setAttribute("data-chip", "edited");
        chip.style.cssText = "display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-family:var(--prizm-font-mono);color:var(--prizm-color-warning)";
        chip.innerHTML = '<span style="width:5px;height:5px;border-radius:9px;background:var(--prizm-color-warning)"></span>Edited by you';
        f.appendChild(chip);
      }
      if (e.target.hasAttribute("data-desc")) {
        const c = document.querySelector("[data-desc-count]");
        if (c) c.textContent = (800 - e.target.value.length) + " characters remaining";
      }
      if (e.target.hasAttribute("data-manual-select") && e.target.value) {
        e.target.style.borderColor = "var(--prizm-color-border-strong)";
        e.target.style.background = "var(--prizm-color-surface)";
        const chip = e.target.closest("[data-prov]").querySelector("[data-chip]");
        if (chip) {
          chip.innerHTML = '<span style="width:5px;height:5px;border-radius:9px;background:var(--prizm-color-success)"></span>Selected by you';
          chip.style.color = "var(--prizm-color-success)";
        }
        this.setState({ err: false });
      }
    });
    root.addEventListener("change", (e) => {
      if (e.target.hasAttribute("data-manual-select")) root.dispatchEvent(new Event("input", { bubbles: true }));
    }, true);

    // assets table: expand / collapse component sub-rows
    root.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-expand]");
      if (!btn) return;
      const key = btn.getAttribute("data-expand");
      const open = btn.getAttribute("data-open") === "1";
      btn.setAttribute("data-open", open ? "0" : "1");
      btn.style.transform = open ? "rotate(0deg)" : "rotate(90deg)";
      document.querySelectorAll('[data-child="' + key + '"]').forEach((r) => {
        r.style.display = open ? "none" : "table-row";
      });
    });
  }

  renderVals() {
    const s = this.state.screen;
    return {
      onHome: s === "home",
      homeWeight: s === "home" ? "600" : "500",
      homeColor: s === "home" ? "var(--prizm-color-fg)" : "var(--prizm-color-fg-muted)",
      assetsWeight: s === "assets" ? "600" : "500",
      assetsColor: s === "assets" ? "var(--prizm-color-fg)" : "var(--prizm-color-fg-muted)",
      onForm: s === "form",
      onAssets: s === "assets",
      toast: this.state.toast,
      registered: !!this.state.registered || this.props.startScreen === "assets",
      err: this.state.err,
      showRail: this.props.showSourceRail !== false,
      showLegend: this.props.showProvenanceLegend !== false,
      goHome: () => this.go("home"),
      goForm: () => this.go("form"),
      goAssets: () => this.go("assets"),
      submit: () => this.submit(),
      dismiss: () => this.setState({ toast: false })
    };
  }
}
</script>


</body></html>