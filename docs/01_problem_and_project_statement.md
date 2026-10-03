# 01 Problem and project statement

*BMED Chapter 3: defining the medical problem, writing the project statement and objective statement, and analysing stakeholders.*

## 1. Clinical problem

Hypotension in the operating room and ICU is common and harmful. Long exposure to low pressure is linked to organ injury. Walsh et al. (*Anesthesiology* 2013) found that even short periods below a mean arterial pressure (MAP) of 55 mmHg were associated with acute kidney injury and myocardial injury. Surviving Sepsis Campaign guidelines set MAP ≥ 65 mmHg as the initial resuscitation target. A conventional bedside monitor raises an alarm only **after** MAP has crossed a threshold. It does not show where the pressure is heading, and it does not show whether a vasopressor or fluid bolus is working.

Monitors also generate many alarms that need no action. Sendelbach & Funk (*AACN Advanced Critical Care* 2013) report that 72–99% of clinical alarms are false or non-actionable. Alarm fatigue is a recognised patient-safety hazard (Joint Commission NPSG.06.01.01). Any new alert must therefore earn its place: it has to be earlier or more informative, without adding noise.

## 2. The technology push: the Version 5 model

The project begins from an existing capability: the physics-informed model developed in Version 5. BMED Breakout Box 3.2 calls this a *technology push* project, as opposed to *market pull*. The book warns that such projects must check that a real need exists, so the need is stated first (above) and every specification is traced to a need (doc 02).

The model reads a 10 s window of bedside-monitor waveforms. PPG is required. ECG, invasive arterial pressure (ABP) and an intervention channel are optional. From these it predicts:

- arterial pressure for each of the next 15 s, and the forecast MAP;
- estimates of vascular resistance (R) and arterial compliance (C) from a two-element Windkessel physics layer;
- signal quality and warnings.

## 3. Project statement (BMED Breakout Box 3.11 MadLib)

> When we heard from **the concept brief and the critical-care and anaesthesia literature**, we learned that **clinicians see hypotension on the monitor only after it has happened, and cannot easily tell whether a treatment is working** (observations).
> This got us thinking about **early, explained warning of haemodynamic deterioration** that impacts **ICU and operating-room nurses and physicians, and their patients**. As we explored more, we found **that short exposure to MAP < 55 mmHg is associated with kidney and myocardial injury (Walsh 2013), and that 72–99% of monitor alarms are non-actionable (Sendelbach & Funk 2013)**.
> Current solutions include **threshold alarms on bedside monitors and the FDA-authorised Hypotension Prediction Index (HPI, De Novo DEN160044)**. However, they do not adequately address **early warning together with treatment-response feedback at low cost**, because **threshold alarms are purely reactive, and HPI is a proprietary add-on tied to specific hardware that does not report treatment response or vascular state**.
> We don't know how to address this challenge yet, but we expect the technical barriers to be **forecast accuracy and lead time, signal artefacts, transfer from simulation to real patients, and alarm burden**. However, in meeting the need we would hope to **increase the time between the warning and the hypotension, and decrease the time patients spend below MAP 65 mmHg**, which may also **give clinicians feedback on whether treatment is working and create an auditable record of each episode**. We think we are well positioned to address this challenge by **deploying the verified Version 5 physics-informed model in a decision-support application and selecting the deployment concept with the best measured evidence**.

## 4. Project objective statement

> Within one academic term, using the existing Version 5 model and no additional budget, deliver a working web-application prototype of three deployment concepts, verify each against 15 measurable specifications on held-out simulated and real (VitalDB) patients, and select one concept for development using the Chapter 6 selection methods, with every decision documented in a Design History File.

BMED Section 3.10 asks for an endpoint (prototype plus benchtop verification), a timeline and resources. Clinical validation with users is explicitly **out of scope**; doc 07 §8 gives the plan for it.

## 5. Stakeholders

| Stakeholder | Role | Main interest | Power | Interest |
|-------------|-----------------|---------------|-------|----------|
| ICU / OR nurses | Primary user | Fewer, better alarms; clear reasons | Medium | High |
| Anaesthesiologists, intensivists | Primary user, decision maker | Earlier warning; is treatment working? | High | High |
| Patients | Beneficiary | Less time hypotensive; no harm from false reassurance | Low | High |
| Clinical / biomedical engineering | Maintainer | Integration with monitors, reliability, alarm management | Medium | Medium |
| Hospital IT / cybersecurity | Gatekeeper | Local-only data, no external services | Medium | Medium |
| Quality and risk officers | Secondary user | Audit trail and episode review (Concept C) | Medium | Medium |
| Hospital purchasing / administration | Buyer | Cost, evidence of benefit | High | Low |
| Regulators (FDA) | Gatekeeper | Safety, effectiveness, intended use | High | Medium |
| Monitor manufacturers | Partner / competitor | Data access, competing products (HPI) | High | Low |
| Payers | Payer | Outcome evidence | Medium | Low |

**Power and influence map (BMED Ch. 3 stakeholder diagnostic):**

- *Manage closely* (high power, high interest): physicians. Involve them in the summative usability study.
- *Keep satisfied* (high power, lower interest): regulators, purchasing and manufacturers. Address them with the standards plan (doc 08) and the economic case.
- *Keep informed* (lower power, high interest): nurses and patients. Nurses are the most frequent users and the people who bear alarm fatigue.

## 6. Users and use environment

- **Concept A:** ICU or operating room, a patient with an arterial line, continuous use by a nurse or anaesthesiologist at the bedside, often under time pressure.
- **Concept B:** step-down unit, recovery area or lower-resource ward, with ECG and pulse oximetry only.
- **Concept C:** any setting with stored monitor records, used after the event for handoff, morbidity and mortality review, quality improvement and research.

## 7. Scope limits (stated up front)

- **Decision support only.** The app never recommends a treatment (SPEC-14).
- **Research prototype.** Demonstration data are held-out simulated patients and de-identified VitalDB cases. It has not been used on live patients.
- **No change to the model.** Version 5 is used frozen. Every improvement is an app-layer control that can be traced and tested.
