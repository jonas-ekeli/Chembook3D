# 09 · Implementation roadmap

_Chembook3D specification pack · v1.1 · 2026-09-29. The phase order is confirmed (D48). The content of each phase is derived from the requirement phase tags in [04](04-functional-requirements.md). Each phase ends with a demo to Jonas on the reference example._

## Phase 0 · Setup (before code)
- Proposals P1–P30 are confirmed (D51). Decide Q33 (browser tab first is recommended).
- Create the repository with a uv-managed environment (D55) and CI on Windows and Linux (P28).
- Request sample output files (R1).

## Phase 1 · Foundation
**Goal:** investigations, manual nodes, geometry and history working end to end.
- FR-INV-01…03, FR-NODE-01…08, FR-HIST-01…03, FR-3D-01 (basic viewer), FR-3D-06 (copy and save xyz), FR-SET-01 (unit setting)
- Backend domain services for ID-4 and ID-5; schema and migrations (P21); lock file (P22)
- A simple node list in the UI (the canvas comes in phase 3)
- **Tests:** T-ID-01, 02, 07; T-OPS-01, 03, 04, 05
- **Exit:** create nodes by hand, edit xyz, see derived nodes and history, zip and reopen on the other OS.

## Phase 2 · Gaussian import
**Needs:** R1 fixtures, P16 decision, a Q32 default.
- FR-CALC-01…05, FR-IMP-01…05, 08, 09, 12, 13, FR-FILE-01…04
- Import preview UI; custom basis naming; composite levels
- **Tests:** T-IMP-01…08, 11, T-ID-03…06, T-OPS-02
- **Exit:** all Gaussian fixtures import correctly; failed and partial files behave as specified.

## Phase 3 · Canvas and 3D
- FR-STEP-01…03, FR-BR-01…04, FR-EDGE-01…03, FR-GRP-01, 02, 04, 05, FR-CAN-01, 02, 04 (compact and structure modes), 05, 06, 07, FR-3D-02…04
- **Tests:** T-BR-01…04, 10…14; T-UI-02, 03, 04 (canvas image)
- **Exit:** Jonas rebuilds the four-branch Ru-CAAC example with a reconnection group. **Review point:** confirm P2 and P5 against real use.

## Phase 4 · Energies
**Needs:** the reference script and its outputs (R1), and Q34.
- FR-EN-01…09, FR-GRP-03, FR-CAN-03, FR-CAN-04 (energy mode)
- Thermochemistry module (composite G, G_qh Truhlar), profile drawer, energy table and CSV, profile image export
- **Tests:** T-EN-01…11, T-BR-05…09, T-UI-04 (profile)
- **Exit:** free-energy profiles of the example branches at one level, cross-checked against GoodVibes.

## Phase 5 · ORCA, xTB, CREST and the resume overview
**Needs:** Q11 scope for these programs; R1 fixtures for them.
- FR-IMP-06, 07, 10, 11; FR-OV-01
- **Tests:** T-IMP-09, 10; T-UI-01
- **Exit:** a mixed-program investigation (CREST ensemble → xTB → Gaussian DFT → ORCA single point) is traceable on the canvas; the overview shows the open items.

**First version (v1) = phases 1–5.**

## Phase 6 · Later (not designed)
Internet hosting, login, collaboration and sync (X2, X4). These need their own discovery round. The phase 1–5 architecture keeps them possible by putting all rules in the backend API (06 §2).

## Dependencies

```
Phase 0 ─► 1 ─► 2 ─► 3 ─► 4 ─► 5
          │    ▲         ▲
          └─R1─┴─────────┘ (fixtures feed phase 2 parsers and phase 4 thermochemistry tests)
```
