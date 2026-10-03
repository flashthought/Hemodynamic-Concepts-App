# 08 Standards, regulatory pathway and ethics

*BMED Chapter 11 (standards), Chapter 12 (regulatory requirements: FDA classification, pathways, design controls), Chapter 13 (ethics), and 6.3.1 / 6.3.4 (regulatory and standards screening).*

## 1. Standards that apply to the selected concept (A)

| Standard | Scope | How this design addresses it | Gap before clinical use |
|----------|-------|------------------------------|-------------------------|
| **IEC 60601-1-8** Alarm systems | Alarm priorities, colours, technical vs physiological alarms | Three priorities plus technical SIGNAL CHECK; colour plus shape; persistence and hysteresis; fixed banner position | Audible signals, alarm silencing/pausing, and alarm logging belong to the host monitor |
| **IEC 62304** Medical device software life cycle | Software development, SOUP, risk-based class | SOUP list (doc 05 §4); frozen, checksum-verified model (FMEA R-13); scripted verification; risk controls traced to tests | Likely **software safety class C** (failure could contribute to serious injury). Needs a formal SDP, unit-level verification records and configuration management |
| **ISO 14971** Risk management | Hazard analysis, risk evaluation and control | FMEA (doc 06), 13 risks, controls verified by measurement, risk–benefit statement | Post-market surveillance plan; field data for probabilities |
| **IEC 62366-1** Usability engineering | Use specification, formative and summative evaluation | Use specification (doc 06 §1.1), formative heuristic evaluation | Summative evaluation with users (doc 07 §8, V3) |
| **ISO 13485** Quality management system | Design controls, DHF | This DHF follows the design-control structure (inputs, outputs, verification, reviews) | A QMS would be needed by any manufacturer |
| **ISO 81060-2** Non-invasive sphygmomanometers, clinical validation | Accuracy of BP measurement (mean error ≤ 5 mmHg, SD ≤ 8 mmHg) | Used as the accuracy reference for SPEC-01. A measures against an arterial line; it does not claim to be a BP measurement device | Would apply in full to any non-invasive claim (Concept B) |
| **IEEE 11073 / HL7 FHIR** device data exchange | Monitor connectivity | Not implemented (replay prototype) | Needed for live data |
| **FDA cybersecurity guidance (2023)** | Premarket cybersecurity | Localhost-only server, input-size limits, path-traversal protection, no external calls | Threat model, SBOM, penetration test |

## 2. Regulatory classification and pathway (United States)

**Is it a device?** The FDA's 2022 Clinical Decision Support guidance says that software which *acquires, processes or analyses a medical signal* (for example ECG or arterial waveforms) does **not** meet the non-device CDS criteria. All three concepts process physiological waveforms, so **all three are devices**, including Concept C.

| | Concept A | Concept B | Concept C |
|---|---|---|---|
| Likely class | II | II | II (possibly lower risk within II) |
| Predicate / product code | Hypotension Prediction Index: De Novo **DEN160044**, regulation **21 CFR 870.2210** (adjunctive predictive cardiovascular indicator) | Same regulation, but non-invasive pressure estimation adds ISO 81060-2 accuracy claims | 870.2210 or a physiological-data review classification |
| Pathway | **510(k)** with HPI as predicate (substantial equivalence: same intended use, predictive indicator of hypotension; different technology → performance data) | 510(k) or De Novo with new clinical data; current evidence fails the needed accuracy | 510(k) |
| Evidence needed | Retrospective + prospective performance study; software documentation (IEC 62304 class C); human factors | All of A's, plus ISO 81060-2 clinical validation | Software documentation; performance of the retrospective analysis |
| Regulatory risk (selection criterion C8) | Medium | High | Lower |

**Labelling consequence of this project's evidence.** The Version 5 intended-use statement (`Version 5/docs/samd/intended_use.md`) describes the model as processing **non-invasive** ECG + PPG data, indicated "when continuous invasive arterial line monitoring is either unavailable, contraindicated, or awaiting placement". **The verification in this project does not support that claim.** On held-out real patients the ECG + PPG configuration reached AUROC 0.59 and MAE 12.6 mmHg, and under-alerted during 100% of critical-hypotension time in simulation. The intended use should be revised to match Concept A:

> *Adjunctive decision support for adult patients in the operating room or ICU **with an indwelling arterial line**, providing a 15-second forecast of mean arterial pressure, an alert when the forecast indicates hypotension, and a display of the haemodynamic response after clinician-recorded interventions. Not for use as the sole basis for diagnosis or treatment. Not a replacement for the arterial-line pressure display or its alarms.*

The Version 5 statement also says the device allows "proactive clinical intervention, such as the administration of vasopressors or fluid resuscitation". This app deliberately reports observations and trends only (SPEC-14), so that it does not suggest a therapy.

## 3. Ethics (BMED Chapter 13)

| Principle | Concern in this project | Design response |
|-----------|-------------------------|-----------------|
| Non-maleficence | False reassurance is the most harmful failure (H-09) | Fail-safe design: RC-08 and RC-10 ensure that losing information can never lower an alarm; SPEC-15 makes this a must-meet requirement |
| Beneficence | An alert that adds noise without benefit wastes attention | Benefits measured honestly against a fair datum, including where the design does *not* beat current practice (persistence benchmark, lead time) |
| Autonomy / clinician authority | Automation bias; the software could appear to decide | Decision support only; every output explains its basis; no treatment recommendations (verified by scan, 0 found) |
| Justice | Concept B was motivated by lower-resource settings | B was not deployed because a tool that is wrong more often in under-resourced settings would widen, not close, the gap. Kept as a research goal |
| Honesty in reporting | Pressure to present a winning concept | Selection weaknesses reported: C wins 66% of random weightings; A's lead time is seconds; A fails SPEC-04 and 08 |
| Data privacy | Patient waveforms | Only de-identified public data (VitalDB) and simulation; server binds to localhost; no telemetry |
| Equity of data | VitalDB is one surgical centre in Korea | Stated as a threat to validity; validation plan includes other cohorts |
| Conflicts of interest | None declared | – |

## 4. Intellectual property (BMED 6.3.5)

Possibly novel: combining a physics-informed (Windkessel-constrained) forecaster with a fail-safe alarm layer (confidence-capped priority, evidence-ranked hold, measured-pressure floor). The HPI patent family (Edwards Lifesciences) would need a freedom-to-operate review before commercialisation. No filing is planned for this academic project.
