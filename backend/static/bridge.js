/* MAR frontend <-> backend bridge.
 *
 * The frontend is a read-only Claude-Design export: a React "dc-runtime"
 * template with every value baked in as a literal and no network code. This
 * script is injected into the *response* by GET /app (the file on disk is
 * never modified) and turns that mockup into a live client.
 *
 * Two properties of the host page dictate the approach:
 *
 *  1. Screens are `sc-if` blocks driven by renderVals(), so React rebuilds a
 *     whole screen subtree on every setState. Everything here is therefore
 *     idempotent and re-runs from a MutationObserver after each commit.
 *
 *  2. The template passes value="..." to React with no onChange handler, which
 *     makes every field a read-only controlled input. We replace each control
 *     with a clone: a cloned node carries no React fiber, so React's delegated
 *     listeners and its controlled-value restore both ignore it, and the field
 *     becomes freely editable.
 *
 * Override the API origin with window.MAR_API_BASE before this script runs.
 */
(function () {
  "use strict";

  var API = String(window.MAR_API_BASE || window.location.origin).replace(/\/+$/, "");
  var DEBUG = /[?&]mardebug\b/.test(window.location.search);

  function log() {
    if (DEBUG) console.debug.apply(console, ["[mar]"].concat([].slice.call(arguments)));
  }
  function warn() {
    console.warn.apply(console, ["[mar]"].concat([].slice.call(arguments)));
  }

  // ---------------------------------------------------------------- DOM utils

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return [].slice.call((root || document).querySelectorAll(sel));
  }

  /** Text of an element's own direct text-node children (ignores nested spans). */
  function ownText(el) {
    if (!el) return "";
    var out = "";
    for (var i = 0; i < el.childNodes.length; i++) {
      if (el.childNodes[i].nodeType === 3) out += el.childNodes[i].nodeValue;
    }
    return out.trim();
  }

  /** First element matching `sel` whose textContent matches `re`. */
  function byText(sel, re, root) {
    var nodes = $$(sel, root);
    for (var i = 0; i < nodes.length; i++) {
      if (re.test(nodes[i].textContent || "")) return nodes[i];
    }
    return null;
  }

  function setText(el, value) {
    if (el && el.textContent !== value) el.textContent = value;
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  /**
   * Cut a form control loose from React so the reviewer can actually type in it.
   *
   * The template gives every control a `value` prop and no onChange, which makes
   * it a controlled input: React reverts each keystroke via restoreControlledState.
   * Cloning alone is not enough — React re-adopts the clone on its next commit
   * (fiber.stateNode ends up pointing at it) — so we also delete the React
   * expandos and the value tracker it restores through. This is re-applied on
   * every pass because React can re-attach them.
   */
  function freeFromReact(node) {
    if (!node) return node;
    if (node.dataset.marFree !== "1") {
      var clone = node.cloneNode(true);
      clone.dataset.marFree = "1";
      if (node.parentNode) node.parentNode.replaceChild(clone, node);
      node = clone;
    }
    Object.keys(node).forEach(function (key) {
      if (key.indexOf("__react") === 0) {
        try { delete node[key]; } catch (e) { /* non-configurable */ }
      }
    });
    if (node._valueTracker) {
      try { delete node._valueTracker; } catch (e) { /* ignore */ }
    }
    return node;
  }

  // ---------------------------------------------------------------- API calls

  function request(path, options) {
    return fetch(API + path, options).then(function (res) {
      return res.text().then(function (body) {
        if (!res.ok) {
          var detail = body;
          try { detail = JSON.parse(body).detail || body; } catch (e) { /* text */ }
          var err = new Error(detail || res.statusText);
          err.status = res.status;
          throw err;
        }
        return body ? JSON.parse(body) : null;
      });
    });
  }

  function apiGet(path) {
    return request(path, { headers: { Accept: "application/json" } });
  }

  function apiPost(path, payload) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload)
    });
  }

  // ------------------------------------------------------------------- state

  var state = {
    summary: null,
    review: null,
    assets: null,
    categories: null,
    search: "",
    toastMessage: "",
    newAssetNo: null,
    uploadStatus: "",
    // Values the reviewer has actually entered. The mockup calls setState on
    // some field interactions, which makes React rebuild the whole form and
    // drop every edit, so these are the source of truth once a field is
    // touched and get written back after each re-render.
    formValues: {}
  };

  // Row templates lifted from the mockup's own table so Prizm styling survives.
  var rowTemplates = { expandable: null, plain: null, child: null };

  var NEW_BADGE_STYLE =
    "padding:2px 7px;border-radius:99px;background:color-mix(in oklab, " +
    "var(--prizm-color-accent) 13%, var(--prizm-color-surface));font-size:10.5px;" +
    "font-weight:600;color:var(--prizm-color-accent)";

  // ------------------------------------------------------- field <-> label map

  // The review form has no name attributes; each field is
  //   <div data-prov><div><span>LABEL<span> *</span></span></div><control></div>
  // so the label's own text is the stable key.
  var LABEL_TO_FIELD = {
    "Purchase type": "purchase_type",
    "Invoice No.": "invoice_no",
    "PO No.": "po_no",
    "DO No.": "do_no",
    "Brand/Model": "name",
    "Vendor/Supplier": "vendor",
    "Quantity": "quantity",
    "Period Contract": "period_contract",
    "Project": "project",
    "MINDEF Category": "category",
    "CAT B or C or Dev?": "mindef_cat",
    "Taggable": "taggable",
    "Description": "description",
    "GL Date": "gl_date",
    "DO Date": "do_date",
    "Tag Number": "tag_no",
    "Serial No.": "serial_no",
    "Unit Price": "price",
    "Asset capitalisation Date": "asset_capitalisation_date",
    "Material Number": "material_number",
    "Receiving Plant": "receiving_plant",
    "Receiving SLOC": "receiving_sloc",
    "Assignee": "custodian"
  };

  var PROV_CHIP = {
    ai: { text: "Extracted", color: "var(--prizm-color-accent)" },
    system: { text: "System default", color: "var(--prizm-color-fg-subtle)" },
    manual: { text: "Needs manual entry", color: "var(--prizm-color-warning)" },
    edited: { text: "Entered by you", color: "var(--prizm-color-success)" }
  };

  function labelOf(block) {
    return ownText($("span", block));
  }

  function controlOf(block) {
    return $("input, select, textarea", block);
  }

  function fieldBlocks() {
    return $$("[data-prov]").filter(function (b) { return LABEL_TO_FIELD[labelOf(b)]; });
  }

  // ------------------------------------------------------------- form filling

  function setControlValue(control, value) {
    if (!control) return;
    if (control.tagName === "SELECT") {
      var wanted = String(value == null ? "" : value).trim();
      var match = null;
      for (var i = 0; i < control.options.length; i++) {
        var opt = control.options[i];
        if (opt.value === wanted || (opt.textContent || "").trim() === wanted) {
          match = opt;
          break;
        }
      }
      control.value = match ? match.value : (wanted ? control.value : "");
      return;
    }
    control.value = value == null ? "" : String(value);
  }

  /** Show the field's provenance as a chip, reusing the mockup's chip styling. */
  function setProvenanceChip(block, prov) {
    var spec = PROV_CHIP[prov] || PROV_CHIP.ai;
    var chip = $('[data-chip="prov"]', block);
    if (chip && chip.dataset.marProv === prov) return; // already correct
    if (!chip) {
      chip = document.createElement("span");
      chip.setAttribute("data-chip", "prov");
      block.appendChild(chip);
    }
    chip.dataset.marProv = prov;
    chip.style.cssText =
      "display:inline-flex;align-items:center;gap:5px;font-size:10.5px;" +
      "font-family:var(--prizm-font-mono);color:" + spec.color;
    chip.innerHTML =
      '<span style="width:5px;height:5px;border-radius:9px;background:' +
      spec.color + '"></span>';
    chip.appendChild(document.createTextNode(spec.text));
  }

  /** Repoint the MINDEF Category select at the backend's real taxonomy. */
  function fillCategoryOptions(select) {
    if (!state.categories || !select || select.dataset.marOptions === "1") return;
    var sample = select.options[0];
    var current = select.value;
    select.innerHTML = "";
    state.categories.forEach(function (cat) {
      var opt = sample ? sample.cloneNode(false) : document.createElement("option");
      opt.value = cat.name;
      opt.textContent = cat.name;
      select.appendChild(opt);
    });
    select.dataset.marOptions = "1";
    setControlValue(select, current);
  }

  function applyExplanation(explanation) {
    var pop = $("[data-ai-pop]");
    if (!pop) return;
    var panel = pop.firstElementChild;
    if (!panel) return;
    var sections = [].slice.call(panel.children);
    var header = sections[0], list = sections[1], footer = sections[2];

    if (header) setText($$("span", header).pop(), (explanation && explanation.title) || "Classification rationale");

    if (list) {
      if (!list.dataset.marTpl && list.firstElementChild) {
        list.dataset.marTpl = list.firstElementChild.outerHTML;
      }
      var entryTemplate = list.dataset.marTpl || null;
      var citations = (explanation && explanation.citations) || [];
      list.innerHTML = "";
      if (citations.length && entryTemplate) {
        citations.forEach(function (cite) {
          var wrap = document.createElement("div");
          wrap.innerHTML = entryTemplate;
          var entry = wrap.firstElementChild;
          var spans = $$("span", entry);
          setText(spans[0], cite.source_label);
          setText(spans[1], cite.quote);
          list.appendChild(entry);
        });
      } else if (entryTemplate) {
        // The pipeline recorded a rationale but no quotable source spans. Say
        // so rather than showing invented quotes.
        var only = document.createElement("div");
        only.innerHTML = entryTemplate;
        var node = only.firstElementChild;
        var s = $$("span", node);
        setText(s[0], "Rationale");
        setText(
          s[1],
          (explanation && explanation.rationale) ||
            "No rationale was recorded for this classification."
        );
        list.appendChild(node);
      }
    }

    if (footer) {
      var spans = $$("span", footer);
      setText(spans[0], (explanation && explanation.runner_up)
        ? "Second-best: " + explanation.runner_up + "."
        : "");
      var n = (explanation && explanation.source_count) || 0;
      setText(spans[1], plural(n, "source"));
    }
  }

  function applyForm() {
    var review = state.review;
    var blocks = fieldBlocks();
    if (!blocks.length) return;

    blocks.forEach(function (block) {
      var key = LABEL_TO_FIELD[labelOf(block)];
      var control = freeFromReact(controlOf(block));
      if (!control) return;
      if (key === "category") fillCategoryOptions(control);
      if (!review) return;
      var field = review.fields[key];
      if (!field) return;
      // A touched field keeps the reviewer's value; an untouched one shows what
      // the pipeline extracted. Never fight the control being typed into.
      var edited = Object.prototype.hasOwnProperty.call(state.formValues, key);
      var wanted = edited ? state.formValues[key] : field.value;
      if (String(control.value) !== String(wanted)) {
        // React may have reverted the field while it still holds focus, so
        // restore regardless — just put the caret back where it was.
        var caret = null;
        if (document.activeElement === control &&
            typeof control.selectionStart === "number") {
          // A caret sitting at the end of the reverted (shorter) value belongs
          // at the end of the restored one, not at the same index.
          caret = control.selectionStart === String(control.value).length
            ? null
            : [control.selectionStart, control.selectionEnd];
        }
        var wasFocused = document.activeElement === control;
        setControlValue(control, wanted);
        if (caret) {
          try { control.setSelectionRange(caret[0], caret[1]); } catch (e) { /* not text */ }
        } else if (wasFocused && typeof control.selectionStart === "number") {
          try {
            control.setSelectionRange(control.value.length, control.value.length);
          } catch (e) { /* not text */ }
        }
      }
      // A field the reviewer has filled in is no longer "needs manual entry".
      var shown = (edited && String(wanted).trim()) ? "edited" : field.prov;
      block.setAttribute("data-prov", field.prov);
      setProvenanceChip(block, shown);
    });

    if (!review) return;

    // Header: "20 of 23 fields pre-filled from purchase documents".
    var counts = review.counts || {};
    var prefilled = (counts.ai || 0) + (counts.system || 0);
    var total = Object.keys(review.fields || {}).length;
    var header = byText("span", /^\d+ of \d+ fields pre-filled/);
    if (header) {
      setText(header, prefilled + " of " + total +
        " fields pre-filled from purchase documents");
    }

    // Rail tallies.
    [["Extracted", counts.ai || 0], ["System default", counts.system || 0],
     ["Manual entry", counts.manual || 0]].forEach(function (pair) {
      var row = $$("div").filter(function (d) {
        return d.children.length === 2 && ownText(d.children[0]) === pair[0];
      })[0];
      if (row) setText(row.children[1], plural(pair[1], "field"));
    });

    // Source-documents rail.
    applySourceDocs(review.source_documents || []);
    applyExplanation(review.explanation);

    // The description counter is the mockup's own; nudge it after refilling.
    var desc = $("[data-desc]");
    if (desc) desc.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function applySourceDocs(docs) {
    var rail = byText("span", /^Source documents$/);
    if (!rail) return;
    var list = rail.parentNode && rail.parentNode.children[1];
    if (!list || !list.firstElementChild) return;
    if (!list.dataset.marTpl) {
      list.dataset.marTpl = list.firstElementChild.outerHTML;
    }
    if (list.dataset.marDocs === String(docs.length) + ":" + docs.map(function (d) {
      return d.title;
    }).join("|")) return;

    var tpl = list.dataset.marTpl;
    list.innerHTML = "";
    docs.forEach(function (doc) {
      var wrap = document.createElement("div");
      wrap.innerHTML = tpl;
      var entry = wrap.firstElementChild;
      var spans = $$("span", entry);
      setText(spans[0], doc.title);
      setText(spans[1], doc.meta || "");
      list.appendChild(entry);
    });
    if (!docs.length) {
      var empty = document.createElement("div");
      empty.innerHTML = tpl;
      var node = empty.firstElementChild;
      var s = $$("span", node);
      setText(s[0], "No source documents");
      setText(s[1], "Upload an invoice to populate this record.");
      list.appendChild(node);
    }
    list.dataset.marDocs = String(docs.length) + ":" + docs.map(function (d) {
      return d.title;
    }).join("|");
    ensureUploadControl(list);
  }

  // ------------------------------------------------------------ assets table

  function captureRowTemplates(tbody) {
    var rows = $$("tr", tbody);
    rows.forEach(function (row) {
      if (row.hasAttribute("data-child")) {
        if (!rowTemplates.child) rowTemplates.child = row.cloneNode(true);
        return;
      }
      if ($("[data-expand]", row)) {
        if (!rowTemplates.expandable) rowTemplates.expandable = row.cloneNode(true);
      } else if (!rowTemplates.plain) {
        rowTemplates.plain = row.cloneNode(true);
      }
    });
    return rowTemplates.expandable && rowTemplates.child;
  }

  function fillCells(row, asset, isChild) {
    var cells = $$("td", row);
    if (cells.length < 8) return row;

    // Cell 1: name (plus a NEW badge on a just-registered asset).
    var nameCell = cells[1];
    if (isChild) {
      nameCell.textContent = asset.name;
    } else {
      nameCell.innerHTML = "";
      var wrap = document.createElement("div");
      wrap.style.cssText = "display:flex;align-items:center;gap:8px";
      var label = document.createElement("span");
      label.style.fontWeight = "600";
      label.textContent = asset.name;
      wrap.appendChild(label);
      if (asset.no === state.newAssetNo) {
        var badge = document.createElement("span");
        badge.style.cssText = NEW_BADGE_STYLE;
        badge.textContent = "NEW";
        wrap.appendChild(badge);
      }
      nameCell.appendChild(wrap);
    }

    setText(cells[2], asset.tag_no || "—");
    setText(cells[3], asset.category || "—");
    setText(cells[4], asset.serial_no || "—");
    setText(cells[5], asset.custodian || "—");
    setText(cells[6], asset.unit_price || "—");

    // Cell 7 keeps the mockup's status pill; only its text changes.
    var pill = $("span", cells[7]);
    if (pill) setText(pill, asset.status || "—");
    else setText(cells[7], asset.status || "—");
    return row;
  }

  function buildAssetRow(asset) {
    var hasChildren = asset.components && asset.components.length;
    var source = hasChildren ? rowTemplates.expandable
                             : (rowTemplates.plain || rowTemplates.expandable);
    var row = source.cloneNode(true);
    var button = $("[data-expand]", row);
    if (hasChildren) {
      if (button) {
        // Start collapsed; the mockup's own delegated handler does the toggling.
        button.setAttribute("data-expand", "mar-" + asset.no);
        button.setAttribute("data-open", "0");
        button.style.transform = "rotate(0deg)";
      }
    } else if (button && button.parentNode) {
      button.parentNode.removeChild(button);
    }
    return fillCells(row, asset, false);
  }

  function buildComponentRow(parent, child) {
    var row = rowTemplates.child.cloneNode(true);
    row.setAttribute("data-child", "mar-" + parent.no);
    row.style.display = "none"; // collapsed to match data-open="0"
    return fillCells(row, child, true);
  }

  function applyAssets() {
    var tbody = $("tbody");
    if (!tbody) return;

    // Keep the search box usable and holding the current term across re-renders.
    var search = $('input[placeholder^="Search by tag number"]');
    if (search) {
      search = freeFromReact(search);
      if (document.activeElement !== search && search.value !== state.search) {
        search.value = state.search;
      }
    }

    if (!captureRowTemplates(tbody)) return;
    if (!state.assets) return;

    var signature = state.assets.total + ":" + state.newAssetNo + ":" +
      state.assets.assets.map(function (a) { return a.no; }).join(",");
    if (tbody.dataset.marSig === signature) return;

    var frag = document.createDocumentFragment();
    state.assets.assets.forEach(function (asset) {
      frag.appendChild(buildAssetRow(asset));
      (asset.components || []).forEach(function (child) {
        frag.appendChild(buildComponentRow(asset, child));
      });
    });
    tbody.innerHTML = "";
    tbody.appendChild(frag);
    tbody.dataset.marSig = signature;

    var heading = byText("span", /records · updated/);
    if (heading) {
      setText(heading, state.assets.total.toLocaleString() +
        (state.assets.total === 1 ? " record · updated " : " records · updated ") +
        formatUpdated(state.assets.updated_at));
    }
  }

  function formatUpdated(iso) {
    if (!iso) return "never";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    return pad(d.getDate()) + " " + months[d.getMonth()] + " " + d.getFullYear() +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  // -------------------------------------------------------------- home screen

  function applyHome() {
    var summary = state.summary;
    if (!summary) return;

    var pending = byText("span", /^\d+ pending$/);
    if (pending) setText(pending, summary.pending + " pending");

    (summary.tiles || []).forEach(function (tile) {
      var label = byText("span", new RegExp("^" + tile.label + "$"));
      if (!label || !label.parentNode) return;
      var spans = $$("span", label.parentNode);
      if (spans[1]) setText(spans[1], tile.caption);
      if (spans[2]) setText(spans[2], plural(tile.count, "asset"));
    });

    // The action item's subtitle should describe the record actually waiting.
    var review = state.review;
    if (review) {
      var subtitle = byText("span", /· Invoice |^No record awaiting review$/);
      if (subtitle) {
        var parts = [review.fields.name && review.fields.name.value];
        if (review.fields.invoice_no && review.fields.invoice_no.value) {
          parts.push("Invoice " + review.fields.invoice_no.value);
        }
        if (review.fields.po_no && review.fields.po_no.value) {
          parts.push("PO " + review.fields.po_no.value);
        }
        setText(subtitle, parts.filter(Boolean).join(" · ") || "Awaiting review");
      }
    }
  }

  // ------------------------------------------------------------------- toast

  function applyToast() {
    if (!state.toastMessage) return;
    var body = byText("span", /added to |could not be registered/);
    if (body) setText(body, state.toastMessage);
  }

  // ----------------------------------------------------------------- uploads

  /** The rail has no file input in the mockup, so add one below the list. */
  function ensureUploadControl(list) {
    var host = list && list.parentNode;
    if (!host || $("[data-mar-upload]", host)) {
      updateUploadLabel();
      return;
    }
    var button = document.createElement("button");
    button.setAttribute("data-mar-upload", "1");
    button.type = "button";
    button.style.cssText =
      "margin-top:10px;width:100%;height:30px;display:inline-flex;align-items:center;" +
      "justify-content:center;gap:6px;border-radius:6px;border:1px dashed " +
      "var(--prizm-color-border-strong);background:transparent;color:var(--prizm-color-fg-muted);" +
      "font-size:12px;font-family:var(--prizm-font-sans);cursor:pointer";
    button.textContent = "Add document";

    var input = document.createElement("input");
    input.type = "file";
    input.setAttribute("data-mar-file", "1");
    input.accept = "image/*,.pdf";
    input.style.display = "none";

    button.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () {
      if (input.files && input.files[0]) uploadDocument(input.files[0]);
      input.value = "";
    });

    host.appendChild(button);
    host.appendChild(input);
    updateUploadLabel();
  }

  function updateUploadLabel() {
    var button = $("[data-mar-upload]");
    if (button) button.textContent = state.uploadStatus || "Add document";
  }

  function uploadDocument(file) {
    var form = new FormData();
    form.append("file", file);
    form.append("system", "oxn");
    state.uploadStatus = "Uploading " + file.name + "…";
    updateUploadLabel();

    request("/invoice", { method: "POST", body: form })
      .then(function (res) {
        state.uploadStatus = "Extracting… (job " + res.job_id.slice(0, 8) + ")";
        updateUploadLabel();
        return pollJob(res.job_id);
      })
      .then(function (jobId) {
        state.uploadStatus = "Add document";
        return loadReview(jobId).then(loadAssets).then(loadSummary);
      })
      .catch(function (err) {
        warn("upload failed:", err.message);
        state.uploadStatus = "Upload failed — retry";
        updateUploadLabel();
      })
      .then(scheduleSync);
  }

  function pollJob(jobId, attempt) {
    attempt = attempt || 0;
    if (attempt > 150) return Promise.reject(new Error("extraction timed out"));
    return apiGet("/api/jobs/" + encodeURIComponent(jobId) + "/status")
      .then(function (status) {
        if (status.status === "ready") return jobId;
        if (status.status === "error") throw new Error(status.error);
        return new Promise(function (resolve) { setTimeout(resolve, 2000); })
          .then(function () { return pollJob(jobId, attempt + 1); });
      });
  }

  // ------------------------------------------------------------------ loaders

  function loadSummary() {
    return apiGet("/api/summary")
      .then(function (data) { state.summary = data; })
      .catch(function (err) { warn("summary:", err.message); });
  }

  function loadCategories() {
    return apiGet("/api/categories")
      .then(function (data) { state.categories = data; })
      .catch(function (err) { warn("categories:", err.message); });
  }

  function loadAssets() {
    var query = state.search ? "?search=" + encodeURIComponent(state.search) : "";
    return apiGet("/api/assets" + query)
      .then(function (data) { state.assets = data; })
      .catch(function (err) { warn("assets:", err.message); });
  }

  function loadReview(jobId) {
    var path = jobId ? "/api/review/" + encodeURIComponent(jobId) : "/api/review/latest";
    return apiGet(path)
      .then(function (data) {
        // A different record means the previous reviewer's edits no longer apply.
        if (!state.review || state.review.job_id !== data.job_id) {
          state.formValues = {};
        }
        state.review = data;
      })
      .catch(function (err) {
        // 404 simply means nothing has been extracted yet.
        if (err.status !== 404) warn("review:", err.message);
        state.review = null;
        state.formValues = {};
      });
  }

  // ------------------------------------------------------------------- wiring

  var allowNativeSubmit = false;

  function isSubmitButton(el) {
    return el && el.tagName === "BUTTON" &&
      (el.textContent || "").trim() === "Complete review";
  }

  function onSubmitClick(event) {
    var button = event.target.closest && event.target.closest("button");
    if (!isSubmitButton(button) || allowNativeSubmit) return;

    event.preventDefault();
    event.stopPropagation();

    var review = state.review;
    if (!review) {
      warn("nothing to submit: no review loaded");
      return;
    }
    // Extracted values, overlaid with what's on screen, overlaid with the
    // reviewer's own edits (authoritative even if a re-render beat our sync).
    var values = {};
    Object.keys(review.fields).forEach(function (key) {
      values[key] = review.fields[key].value;
    });
    fieldBlocks().forEach(function (block) {
      var control = controlOf(block);
      if (control) values[LABEL_TO_FIELD[labelOf(block)]] = control.value;
    });
    Object.keys(state.formValues).forEach(function (key) {
      values[key] = state.formValues[key];
    });

    log("submitting review", review.job_id, values);
    button.disabled = true;
    apiPost("/api/review/" + encodeURIComponent(review.job_id) + "/complete",
            { fields: values })
      .then(function (res) {
        state.toastMessage = res.message;
        state.newAssetNo = res.asset.no;
        state.formValues = {};
        var banner = $("[data-mar-error]");
        if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
        return loadAssets().then(loadSummary);
      })
      .then(function () {
        // Let the mockup's own submit() run: it switches screen, sets the
        // `registered` flag and shows the toast for us. The button must be
        // re-enabled first — clicks on a disabled button are never dispatched.
        button.disabled = false;
        // The component's submit() refuses to advance if the CAT select looks
        // empty; React may have just reverted it, so put the value back first.
        var manual = $("[data-manual-select]");
        if (manual && !manual.value) setControlValue(manual, values.mindef_cat);
        allowNativeSubmit = true;
        log("replaying click on", button.isConnected ? "live button" : "detached button");
        button.click();
        allowNativeSubmit = false;
      })
      .catch(function (err) {
        warn("complete review failed:", err.message);
        showSubmitError(err.message);
      })
      .then(function () { button.disabled = false; scheduleSync(); });
  }

  function showSubmitError(message) {
    var select = $("[data-manual-select]");
    if (select && !select.value) {
      // Reuse the mockup's own validation affordance.
      select.style.borderColor = "var(--prizm-color-danger)";
      select.focus();
    }
    var banner = $("[data-mar-error]");
    if (!banner) {
      var anchor = byText("span", /Asset record will be updated/);
      if (!anchor || !anchor.parentNode) return;
      banner = document.createElement("div");
      banner.setAttribute("data-mar-error", "1");
      banner.style.cssText =
        "margin:10px 0;padding:9px 11px;border-radius:6px;font-size:12.5px;" +
        "border:1px solid var(--prizm-color-danger);color:var(--prizm-color-danger);" +
        "background:color-mix(in oklab, var(--prizm-color-danger) 8%, transparent)";
      anchor.parentNode.parentNode.insertBefore(banner, anchor.parentNode);
    }
    banner.textContent = message;
  }

  /** Capture every edit, because a React re-render is about to erase the DOM. */
  function rememberEdit(event) {
    var control = event.target;
    if (!control || !control.closest) return;
    var block = control.closest("[data-prov]");
    if (!block) return;
    var key = LABEL_TO_FIELD[labelOf(block)];
    if (!key) return;
    state.formValues[key] = control.value;
    // Some field interactions make the component call setState, and React's
    // commit can land while the observer is detached mid-apply. Re-sync once
    // the commit has definitely happened so the value is written back.
    setTimeout(scheduleSync, 0);
    setTimeout(scheduleSync, 200);
  }

  var searchTimer = null;

  function onSearchInput(event) {
    var input = event.target;
    if (!input || input.tagName !== "INPUT") return;
    if (!/Search by tag number/.test(input.placeholder || "")) return;
    state.search = input.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      loadAssets().then(scheduleSync);
    }, 250);
  }

  // ------------------------------------------------------------------ syncing

  var observer = null;
  var syncQueued = false;

  function applyAll() {
    if ($("[data-desc]")) applyForm();
    if ($("tbody")) applyAssets();
    applyHome();
    applyToast();
  }

  function sync() {
    syncQueued = false;
    if (observer) observer.disconnect();
    try {
      applyAll();
    } catch (err) {
      warn("apply failed:", err && err.message, err);
    } finally {
      if (observer) {
        observer.observe(document.body, { childList: true, subtree: true });
      }
    }
  }

  function scheduleSync() {
    if (syncQueued) return;
    syncQueued = true;
    requestAnimationFrame(sync);
  }

  // --------------------------------------------------------------------- boot

  function boot() {
    log("bridge starting against", API);
    if (DEBUG) window.__mar = { state: state, sync: scheduleSync, load: loadReview };

    // Capture phase so we run before React's delegated handlers.
    document.addEventListener("click", onSubmitClick, true);
    document.addEventListener("input", onSearchInput);
    document.addEventListener("input", rememberEdit, true);
    document.addEventListener("change", rememberEdit, true);

    observer = new MutationObserver(scheduleSync);
    observer.observe(document.body, { childList: true, subtree: true });

    Promise.all([loadSummary(), loadCategories(), loadAssets(), loadReview()])
      .then(scheduleSync);
  }

  /** The dc-runtime replaces documentElement on boot; wait for its first render. */
  function waitForMount(attempt) {
    attempt = attempt || 0;
    if (document.body && ($("[data-prov]") || $("[data-expand]") ||
        byText("h1", /^Home$/))) {
      boot();
      return;
    }
    if (attempt > 300) {
      warn("frontend never mounted; bridge inactive");
      return;
    }
    setTimeout(function () { waitForMount(attempt + 1); }, 100);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { waitForMount(); });
  } else {
    waitForMount();
  }
})();
