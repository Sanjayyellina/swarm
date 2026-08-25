# Swarm Prompting Guide — the principles behind frontier-grade agents

These are the rules that make a production agent reliable. Every prompt in
`prompts/core/` follows them. When you write a new client prompt, check it
against this list.

## 1. Role + scope + "done" — always, in the first lines
A weak prompt says "you are a helpful assistant." A strong prompt says who the
agent is, who it serves, exactly what it may do, and what a finished job looks
like. If the agent can't tell whether it's done, it will ramble or overreach.

## 2. Enumerate the paths, don't gesture at them
List the concrete situations the agent will face (emergency call / routine
booking / price question / angry customer / wrong number) and what to do in
each. Models follow enumerated branches far better than "use your judgment."
Whatever you don't enumerate, route to the escalation path.

## 3. Tools: say when to use them AND when not to
For every tool: the trigger condition, the required inputs, and the rule for
what to do when inputs are missing (ask — never invent). The #1 production
failure is a model calling a tool with fabricated arguments.

## 4. Never fabricate — give the agent an out
Models fabricate when they have no permitted way to say "I don't know."
Every prompt must include an explicit escape hatch: take a message, escalate
to the owner, say "I'll have someone confirm that." An agent with a good
escape hatch almost never lies.

## 5. Output contracts
If downstream code parses the output, specify the format exactly and forbid
anything else ("Respond with ONLY the JSON object — no prose, no markdown").
For humans, specify tone, length, and what never to include.

## 6. Examples beat adjectives
One worked example of a perfect interaction teaches more than ten adjectives.
Show one ideal exchange and, when there's a common failure, one "never do
this." Keep examples short — they're patterns, not padding.

## 7. Enumerate the edge cases you already know
Wrong number, caller won't give a phone number, asks for a price, asks for a
competitor, abusive caller, request outside service area, request outside the
agent's authority. Discovery meetings with the client fill this section —
every "oh, and if they ask X..." from the owner goes here.

## 8. Guardrails are positive instructions
"Don't be rude" is weaker than "If the caller is frustrated, acknowledge it
in one sentence, then focus on the fastest fix." Tell the agent what TO do in
the hard moment.

## 9. Small models need tighter prompts
On a 4–8B model, cut nuance, shorten branches, use MUST/NEVER lists, and add
one more example. The same job needs a stricter script when the brain is
smaller. Test every prompt on the actual production model, not just the big
dev model.

## 10. Prompts are code: version, test, iterate
Keep prompts in git. After every real transcript that goes wrong, trace it to
the missing branch or unclear rule, fix the prompt, and re-run the transcript.
Ten iterated transcripts produce a better prompt than any first draft —
including this pack's.

## The universal skeleton (all core prompts follow it)

```
[IDENTITY]     who you are, whose behalf you act on
[MISSION]      the one job + what "done" looks like
[PLAYBOOK]     enumerated situations → actions (incl. tool triggers)
[TOOLS]        per-tool: when, required inputs, missing-input rule
[HARD RULES]   MUST / NEVER list (short, absolute)
[ESCAPE HATCH] exactly what to do when nothing above fits
[VOICE]        tone, length, formatting of replies
[EXAMPLE]      one ideal exchange
```
