"""What the 23 registration fields are called and which control each uses.

The canonical field set lives in backend/app/schemas/provenance.py; this is the
design's presentation of it — the labels, tooltips, hints and control types,
copied from the export. Both the review screen and the add-asset screen render
from here, so the two forms cannot drift apart.
"""

from __future__ import annotations


TIP_PURCHASE_TYPE = ("Contracting instrument the asset was bought under: one-time "
                     "purchase, period contract call-off, or demand against a "
                     "framework agreement.")
TIP_CAP_DATE = ("Date the asset enters the fixed asset register and begins "
                "depreciating. Defaults to the delivery date unless commissioning "
                "is later.")
TIP_MATERIAL = ("SAP material master number for this item. Used to link the record "
                "to procurement and stock movement history.")
TIP_PLANT = ("Plant code of the unit that took delivery. Determines which inventory "
             "ledger the asset sits in.")
TIP_SLOC = ("Storage location within the receiving plant — the physical store, room "
            "or rack the asset was booked into.")

PURCHASE_TYPES = ("One-time purchase", "Period contract call-off", "Framework demand")
MINDEF_CAT_OPTIONS = (("", "Select — required"), ("B", "CAT B"), ("C", "CAT C"),
                      ("DEV", "Dev"))
TAGGABLE_OPTIONS = ("Yes", "No")


class Field:
    """One form field: how the design labels it and which control it uses."""

    def __init__(self, key, label, *, required=False, tip="", hint="",
                 mono=False, tabular=False, placeholder="", maxlength=None,
                 control="text", options=()):
        self.key, self.label, self.required = key, label, required
        self.tip, self.hint = tip, hint
        self.mono, self.tabular = mono, tabular
        self.placeholder, self.maxlength = placeholder, maxlength
        self.control, self.options = control, options


VENDOR_FIELDS = (
    Field("purchase_type", "Purchase type", required=True, tip=TIP_PURCHASE_TYPE,
          control="select", options=PURCHASE_TYPES),
    Field("invoice_no", "Invoice No.", required=True, mono=True),
    Field("po_no", "PO No.", mono=True),
    Field("do_no", "DO No.", mono=True),
    Field("name", "Brand/Model", required=True, hint="max 40 chars", maxlength=40),
    Field("vendor", "Vendor/Supplier", required=True),
    Field("quantity", "Quantity", mono=True, tabular=True),
    Field("price", "Unit Price", required=True, mono=True, tabular=True),
    Field("period_contract", "Period Contract", mono=True),
)

MINDEF_FIELDS = (
    Field("project", "Project", required=True),
    Field("category", "MINDEF Category", required=True, control="category"),
    Field("mindef_cat", "CAT B or C or Dev?", required=True, control="mindef_cat"),
    Field("taggable", "Taggable", required=True, control="select",
          options=TAGGABLE_OPTIONS),
)

DESCRIPTION_FIELD = Field("description", "Description", required=True,
                          control="textarea", maxlength=800)

LOGISTICS_FIELDS = (
    Field("gl_date", "GL Date", mono=True, tabular=True),
    Field("do_date", "DO Date", required=True, mono=True, tabular=True),
    Field("tag_no", "Tag Number", mono=True, placeholder="Assigned after tagging"),
    Field("serial_no", "Serial No.", required=True, mono=True),
    Field("asset_capitalisation_date", "Asset capitalisation Date", mono=True,
          tabular=True, tip=TIP_CAP_DATE),
    Field("material_number", "Material Number", mono=True, tabular=True,
          tip=TIP_MATERIAL),
    Field("receiving_plant", "Receiving Plant", mono=True, tip=TIP_PLANT),
    Field("receiving_sloc", "Receiving SLOC", mono=True, tip=TIP_SLOC),
)

CUSTODIAN_FIELD = Field("custodian", "Assignee", required=True)

ALL_FIELDS = (*VENDOR_FIELDS, *MINDEF_FIELDS, DESCRIPTION_FIELD,
              *LOGISTICS_FIELDS, CUSTODIAN_FIELD)

# Chip text and colour per provenance — the live app's mapping.
PROV_CHIP = {
    "ai": ("Extracted", "var(--prizm-color-accent)"),
    "system": ("System default", "var(--prizm-color-fg-subtle)"),
    "manual": ("Needs manual entry", "var(--prizm-color-warning)"),
    "edited": ("Entered by you", "var(--prizm-color-success)"),
}

_LABEL_ROW = ("display:flex;align-items:flex-start;flex-wrap:wrap;gap:2px 5px;"
              "min-height:32px;line-height:1.35")
_SECTION = "padding:20px 22px 22px;display:flex;flex-direction:column;gap:16px"
_H2 = "margin:0;font-size:15px;font-weight:600;letter-spacing:-0.015em"
_DIVIDER = '<div style="height:1px;background:var(--prizm-color-border)"></div>'

