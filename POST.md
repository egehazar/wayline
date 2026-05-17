# Wayline — a behavioral product intelligence engine

## What it is

Wayline ingests raw product event streams and automatically surfaces the activation paths, milestones, and cohort patterns that correlate with retention — then drafts experiment specs from those findings. Where Mixpanel and Amplitude let you query behavior, Wayline discovers which behaviors matter.

## Why it exists

The famous activation insights — Slack's "30 messages in 7 days," Facebook's "7 friends in 10 days," Dropbox's "1 file uploaded" — were all discovered manually. An analyst writes a series of cohort SQL queries, iterates on hypotheses, eyeballs the results, eventually finds the metric. Most companies do this exercise once and never revisit it. Many never do it at all.

The cost is steep. Activation is the largest controllable lever on retention, and it's getting addressed with intuition and one-off analyses instead of a continuous discovery loop.

Wayline closes that loop. The engine mines candidate milestones from raw event streams, scores each against a retention outcome, and packages the strongest signals as experiment specs grounded in the underlying data.

## Methodology — milestone discovery

(To fill in when the engine takes shape: the candidate space, the cohort labeling decision, the statistical scoring, the path analysis.)

## Synthetic data — the latent-structure problem

(To fill in when we design the generator. Why naive uniform-random events produce no signal; how to inject the kind of structure real product data has, so that real milestones can be discovered.)

## LLM-drafted experiment specs

(To fill in when the synthesis stage is built. How to constrain generation so specs are grounded in observed data rather than plausible-sounding hallucinations.)

## Results

(Milestones surfaced, retention lift numbers, time-to-analysis benchmarks, demo artifacts.)

## Limitations

(Honest. To fill in.)
