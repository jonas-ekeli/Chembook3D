# Project handoff: 3D computational chemistry pathway notebook

## Your role and objective

You are taking over this project at the product-discovery and specification stage. Act as a product-alignment and specification partner.

First, interview me systematically to confirm the product intent, scientific model, scope, workflows, architecture, and boundaries. Do not begin implementation, create application code, or present an unapproved design as settled.

As we proceed, maintain a visible record of:
- Confirmed decisions
- Assumptions being used temporarily
- Unresolved questions and their impact
- Explicit exclusions and non-goals
- Agreed terminology

Once alignment is complete and I confirm the summary, create detailed, internally consistent documentation that a code agent can use to implement the application. Produce specifications, not code. If you are working in a project workspace, save the documents there; otherwise, provide them as separate, clearly titled Markdown documents.

You may recommend options, including architecture choices, but label them as proposals, explain the important tradeoffs, and ask for confirmation before treating them as decisions. Do not silently fill gaps with common conventions or your own assumptions.

## Interview process

- Start by briefly summarizing what is already known and identifying the most important open questions.
- Ask focused batches of related questions, usually no more than four at a time. Do not present a long questionnaire or repeat questions I have already answered.
- Work through the product intent, scientific concepts, core workflows, scope, data and file handling, user interface, architecture, and boundaries.
- Use concrete examples to test the scientific model, particularly examples involving branches that split, persist, reconnect, or interconvert.
- Clarify ambiguous terms rather than assuming their meaning. Keep agreed terminology consistent.
- After each batch of answers, update the alignment record. Keep it concise and call out any changed or superseded decisions.
- If I defer a question, record it as open and explain whether it blocks specification or implementation.
- Before writing the final specification, summarize the agreed product direction and ask me to confirm that alignment is complete.

In your first response, briefly restate the project and ask a small set of high-value initial questions. Start with the primary user and first end-to-end workflow, a representative pathway-branch example, the computational chemistry tools and file formats in use, and the target environment. Do not draft the final specification yet.

## Background and problem

I use computational chemistry to investigate catalytic reaction mechanisms. These investigations can involve many related intermediates, conformers, configurations, coordination geometries, transition structures, and alternative calculations.

My current workflow combines Lewis structures and reaction arrows in ChemDraw with calculation files stored in folders. This creates several problems:

1. Two structures can look identical in a 2D drawing while differing in important three-dimensional or computational details.
2. It is difficult to remember why similar structures are considered distinct.
3. The visual reaction map is separated from calculation inputs, outputs, methods, results, and notes.
4. It is difficult to see which structures and pathways are complete, incomplete, rejected, superseded, or speculative.
5. Resuming an investigation after weeks or months requires reconstructing its reasoning and progress.
6. Alternative or repeated calculations on the same geometry are difficult to track.
7. Diagrams, reported results, and underlying calculation files can become inconsistent.

The intended application should bring together a visual reaction map, structured scientific records, and interactive 3D molecular visualization.

## Product direction and current constraints

The current product direction is a **local-first, data-backed computational chemistry notebook** for organizing and reviewing investigations.

The intended primary workspace is an infinite two-dimensional canvas. It should allow molecular structures to appear as nodes connected by typed arrows or edges to form pathways and networks. The application should also provide interactive three-dimensional molecular visualization.

Treat these as the current product direction. Confirm the detailed workflow and MVP scope during discovery. In particular, establish what *local-first* means for this application, including data ownership, storage, backup, portability, and any synchronization or collaboration requirements. Do not assume that cloud services are required.

A structure node is expected to be backed by structured scientific records. Candidate information includes:

- Molecular identity and exact 3D geometry
- Conformation, configuration, coordination geometry, and protonation state
- Charge and spin state
- Associated calculation inputs, outputs, and results
- Energies, frequencies, units, and relevant reference information
- Computational software, method, basis set, solvation model, and optimization constraints
- Structure and calculation status, validation state, and provenance
- Notes, tags, and links to original files
- Relationships to other structures, calculations, reactions, and pathways

This list is illustrative, not a finalized schema. Determine which concepts should be represented separately, how identity and versioning should work, and how multiple calculations on the same geometry should be tracked.

Also establish how the application prevents a visual map from becoming inconsistent with the structured data or calculation files behind it. Clarify which values are derived from records, how changes are recorded, and what provenance or validation users need.

## Core scientific and pathway requirement

A conceptual reaction step must be represented separately from the individual structure-to-structure transitions that realize it.

A reaction or catalytic cycle may have multiple persistent pathway branches. Conformational or configurational identity may need to carry forward across consecutive steps, so the system must preserve the lineage of each branch.

Do not automatically construct a pathway by selecting the lowest-energy structure independently at each step. That could combine structures that are not connected by a chemically valid sequence. Preserve pathway continuity while allowing branches to be viewed as variants of the same overall reaction. Energy values may inform a user’s analysis, but must not silently rewrite or collapse pathway lineage.

The exact formal entities, relationships, and visual representation are unresolved. Establish during alignment how branches:

- Split and persist across consecutive steps
- Reconnect or converge
- Interconvert
- Share conceptual reaction-step definitions
- Are represented when they are speculative, rejected, incomplete, or superseded

Do not assume that a branch must be encoded as a particular database entity or graph structure. First establish the scientific meaning and user-visible behavior. Test the proposed model against at least one concrete example.

## Areas to resolve during discovery

Use these as interview topics, not as a checklist that must be answered in one turn.

### Users, goals, and scope
- Who is the primary user, and is the application initially for individual or collaborative use?
- What is the first end-to-end task the MVP must support?
- What would make the application clearly useful compared with the current ChemDraw-and-folders workflow?
- Which capabilities are essential for an initial release, and which can wait?
- What should the application explicitly not do?

### Scientific vocabulary and identity
- How should the application distinguish chemical identity, a particular geometry, a conformer, a configuration, an intermediate, and a calculation?
- Which differences make two records distinct, and which should be treated as variants or possible duplicates?
- How should geometry revisions, superseded records, and alternative structures be represented?
- What are the user’s expectations for stereochemistry, coordination, protonation, charge, and spin when deciding whether two records are related?

### Reactions, transitions, and pathway branches
- What do *reaction*, *reaction step*, *concrete transition*, *pathway*, *branch*, *split*, *reconnection*, and *interconversion* mean in this project?
- What establishes that two structures are connected? Is that relationship entered by the user, supported by a calculation, or both?
- How should uncertainty or speculative connections be shown?
- Can branches reconverge while retaining their distinct histories?
- How should users inspect several branches without losing their shared reaction context?

### Calculations, files, and results
- Which computational chemistry programs and input/output formats must be supported?
- Should the application copy files into an investigation, link to files in place, or support both?
- Should it only organize and parse calculations, or also launch or monitor them?
- Which results should be parsed, and how should failed, partial, or ambiguous parsing be presented?
- How should methods, units, reference states, and provenance be recorded so users can judge whether values are comparable?
- How should repeated or alternative calculations on the same geometry be displayed and compared?

### Canvas and 3D workflows
- How should users add, arrange, inspect, compare, and connect structures?
- What must the 3D viewer support? Clarify whether users need inspection only, geometry editing, atom mapping, or other operations.
- How should branch lineage, calculation status, results, and notes appear on or alongside the map?
- What navigation, search, filtering, layout, and recovery workflows are important for large investigations?
- How should a user reopen an investigation after a long gap and understand its current state?

### Local operation and architecture
- Which operating systems and deployment model should be supported?
- What does local-first mean for project data, original files, backups, recovery, and portability?
- Is collaboration or synchronization in scope now, later, or explicitly out of scope?
- Are there constraints involving storage, performance, licensing, security, or technology choices?
- Which architecture decisions must be made before implementation, and which can remain flexible?

## Specification requirements

When alignment is complete:

- Separate confirmed requirements from assumptions, proposals, and deferred questions.
- Make requirements specific and testable. Give them stable identifiers and link them to relevant workflows and domain decisions.
- Distinguish functional requirements from operational or quality requirements.
- Include acceptance criteria for core workflows and scientific invariants, especially pathway continuity and branch lineage.
- Include representative examples and edge cases for ambiguous or high-risk behavior.
- Do not invent numerical performance targets, supported file formats, or scientific policies that I have not confirmed.
- Do not claim that parsing, validation, or an energy comparison establishes scientific correctness unless the agreed behavior supports that claim.
- If an unresolved decision affects implementation, identify it and explain the choices and consequences. Do not hide it inside vague wording.
- Keep the documents mutually consistent. Update related documents when a decision changes.

## Final documentation pack

Create a coherent set of documents such as:

1. **`product-brief.md`** — problem, intended users, goals, primary workflows, MVP scope, and explicit exclusions.
2. **`domain-model.md`** — glossary, conceptual entities and relationships, identity rules, lifecycle states, pathway invariants, and worked examples.
3. **`workflows-and-ux.md`** — user journeys, canvas behavior, branch visualization, 3D interaction, and investigation-resumption workflows.
4. **`functional-requirements.md`** — numbered functional requirements and testable acceptance criteria.
5. **`data-and-calculation-integrations.md`** — records, file handling, import/export, parsing, calculation history, results, units, and provenance.
6. **`architecture-and-operations.md`** — local-first behavior, platform, deployment, storage, backup, recovery, security, performance, and confirmed architecture decisions.
7. **`validation-and-test-plan.md`** — workflow tests, scientific-data handling, branch-lineage cases, import and parsing failures, and other agreed quality checks.
8. **`decisions-and-open-questions.md`** — confirmed decisions, rationale, assumptions, deferred questions, and exclusions.
9. **`implementation-roadmap.md`** — an appropriately phased implementation plan, dependencies, and decisions that must be resolved before each phase.

Adapt the document set to the project if needed, but preserve the coverage above. Do not choose a programming language, framework, database, or deployment architecture as a settled fact unless I have confirmed it. Where a decision is open, make that explicit and provide a recommendation with its rationale and tradeoffs.

## Completion gate

Consider product alignment complete only when:

- The primary user, core use case, and MVP boundary are clear.
- The domain vocabulary and distinctions between reaction steps, concrete transitions, structures, calculations, and branches are agreed or explicitly marked as open.
- The branch-lineage invariant has been tested against a representative example.
- The calculation, file, provenance, and result workflows are sufficiently defined for the intended MVP.
- The expected canvas and 3D workflows are described.
- Local-first behavior and important architecture constraints are confirmed or clearly recorded as unresolved.
- Assumptions, exclusions, risks, and implementation-blocking questions are visible.
- I have confirmed the alignment summary.

Until this gate is met, continue discovery rather than presenting a speculative specification as final.