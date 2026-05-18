type Milestone = {
  name: string;
  n_did: number;
  n_did_pct: number;
  retain_did: number;
  retain_didnt: number;
  lift: number;
  persona_dominance: Record<string, number>;
};

type Path = {
  sequence: string[];
  sequence_str: string;
  n_users: number;
  retain_pct: number;
  lift: number;
};

type Spec = {
  milestone_name: string;
  hypothesis: string;
  target_segment: string;
  success_event: string;
  guardrail_metrics: string[];
  expected_effect_size: string;
  rationale: string;
};

const API = "http://localhost:8000";

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) {
    throw new Error(`${path}: HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function getData() {
  try {
    const [milestones, paths, specs] = await Promise.all([
      fetchJson<Milestone[]>("/milestones"),
      fetchJson<Path[]>("/paths"),
      fetchJson<Spec[]>("/specs"),
    ]);
    return { milestones, paths, specs, error: null };
  } catch (e) {
    return {
      milestones: [] as Milestone[],
      paths: [] as Path[],
      specs: [] as Spec[],
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

function topPersona(d: Record<string, number>): { name: string; pct: number } {
  const entries = Object.entries(d);
  if (entries.length === 0) return { name: "—", pct: 0 };
  entries.sort((a, b) => b[1] - a[1]);
  return { name: entries[0][0], pct: entries[0][1] };
}

function prettyMilestone(name: string): string {
  return name.replaceAll("_", " ");
}

export default async function Page() {
  const { milestones, paths, specs, error } = await getData();

  if (error) {
    return (
      <main className="min-h-screen flex items-center justify-center bg-white px-8">
        <div className="max-w-xl">
          <p className="text-xs uppercase tracking-widest text-neutral-400 font-medium">
            Wayline
          </p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-neutral-900">
            Backend unreachable
          </h1>
          <p className="mt-5 text-neutral-600 leading-relaxed">
            Couldn&apos;t reach the Wayline API at{" "}
            <code className="font-mono text-sm bg-neutral-100 px-1.5 py-0.5 rounded text-neutral-800">
              {API}
            </code>
            . Start it from the project root:
          </p>
          <pre className="mt-4 font-mono text-sm bg-neutral-50 border border-neutral-200 rounded-lg p-4 text-neutral-800 overflow-x-auto">
            uv run uvicorn api.main:app --port 8000
          </pre>
          <p className="mt-6 text-sm text-neutral-400">Details: {error}</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-white">
      <div className="max-w-6xl mx-auto px-8 py-16">
        <Header milestonesCount={milestones.length} />
        <MilestonesSection milestones={milestones} />
        <PathsSection paths={paths} />
        <SpecsSection specs={specs} />
        <Footer />
      </div>
    </main>
  );
}

function Header({ milestonesCount }: { milestonesCount: number }) {
  return (
    <header className="border-b border-neutral-200 pb-12 mb-16">
      <p className="text-xs uppercase tracking-widest text-indigo-600 font-semibold">
        Wayline
      </p>
      <h1 className="mt-3 text-4xl font-semibold tracking-tight text-neutral-900 sm:text-5xl">
        Behavioral product intelligence engine
      </h1>
      <p className="mt-4 max-w-2xl text-lg text-neutral-600 leading-relaxed">
        Mines activation milestones and ordered paths from raw event streams,
        then drafts experiment specs grounded in the data.
      </p>

      <dl className="mt-12 grid grid-cols-2 gap-x-6 gap-y-8 sm:grid-cols-4 max-w-4xl">
        <Stat label="Events analyzed" value="370k" />
        <Stat label="Users" value="25k" />
        <Stat label="Milestones surfaced" value={String(milestonesCount)} />
        <Stat label="Pipeline runtime" value="1.6s + 200s LLM" />
      </dl>
    </header>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wider text-neutral-500 font-medium">
        {label}
      </dt>
      <dd className="mt-1.5 text-2xl font-semibold text-neutral-900 tabular-nums">
        {value}
      </dd>
    </div>
  );
}

function SectionHeader({
  eyebrow,
  title,
  desc,
}: {
  eyebrow: string;
  title: string;
  desc: string;
}) {
  return (
    <div className="mb-8">
      <p className="text-xs uppercase tracking-widest text-indigo-600 font-semibold">
        {eyebrow}
      </p>
      <h2 className="mt-2 text-2xl font-semibold tracking-tight text-neutral-900">
        {title}
      </h2>
      <p className="mt-2 text-neutral-600 leading-relaxed">{desc}</p>
    </div>
  );
}

function MilestonesSection({ milestones }: { milestones: Milestone[] }) {
  return (
    <section className="mt-16">
      <SectionHeader
        eyebrow="01"
        title="Activation milestones"
        desc="Top 12 behaviors that correlate with week-4 retention, filtered to cohorts ≤ 25% of the population (specificity gate)."
      />
      <div className="overflow-x-auto rounded-lg border border-neutral-200">
        <table className="min-w-full divide-y divide-neutral-200">
          <thead className="bg-neutral-50">
            <tr>
              <Th align="right">#</Th>
              <Th>Milestone</Th>
              <Th align="right">Cohort</Th>
              <Th align="right">Retain (did)</Th>
              <Th align="right">Retain (didn&apos;t)</Th>
              <Th align="right">Lift</Th>
              <Th>Dominant persona</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100 bg-white">
            {milestones.map((m, i) => {
              const top = topPersona(m.persona_dominance);
              return (
                <tr key={m.name} className="hover:bg-neutral-50/60">
                  <Td align="right" className="text-neutral-400 tabular-nums">
                    {i + 1}
                  </Td>
                  <Td>
                    <div className="font-medium text-neutral-900">
                      {prettyMilestone(m.name)}
                    </div>
                    <div className="font-mono text-xs text-neutral-400">
                      {m.name}
                    </div>
                  </Td>
                  <Td align="right" className="tabular-nums">
                    <div className="text-neutral-900">
                      {m.n_did.toLocaleString()}
                    </div>
                    <div className="text-xs text-neutral-500">
                      {m.n_did_pct.toFixed(1)}%
                    </div>
                  </Td>
                  <Td align="right" className="tabular-nums text-neutral-900">
                    {(m.retain_did * 100).toFixed(1)}%
                  </Td>
                  <Td align="right" className="tabular-nums text-neutral-500">
                    {(m.retain_didnt * 100).toFixed(1)}%
                  </Td>
                  <Td align="right" className="tabular-nums">
                    <span className="font-semibold text-indigo-600">
                      {m.lift.toFixed(2)}×
                    </span>
                  </Td>
                  <Td>
                    <div className="text-neutral-900 capitalize">
                      {top.name}
                    </div>
                    <div className="text-xs text-neutral-500 tabular-nums">
                      {top.pct.toFixed(0)}% of cohort
                    </div>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      scope="col"
      className={`px-4 py-3 text-xs uppercase tracking-wider text-neutral-500 font-medium ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = "left",
  className = "",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <td
      className={`px-4 py-3 text-sm ${
        align === "right" ? "text-right" : "text-left"
      } ${className}`}
    >
      {children}
    </td>
  );
}

function PathsSection({ paths }: { paths: Path[] }) {
  return (
    <section className="mt-20">
      <SectionHeader
        eyebrow="02"
        title="Common activation paths"
        desc="Top 10 ordered five-event prefixes among users who reached ≥ 5 post-signup events. Lift is relative to the candidate-pool base rate."
      />
      <ol className="space-y-3">
        {paths.map((p, i) => (
          <li
            key={p.sequence_str}
            className="rounded-lg border border-neutral-200 bg-white px-5 py-4 sm:flex sm:items-start sm:justify-between sm:gap-6 hover:bg-neutral-50/60 transition-colors"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-3">
                <span className="text-xs tabular-nums text-neutral-400 font-medium w-6 shrink-0">
                  {(i + 1).toString().padStart(2, "0")}
                </span>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  {p.sequence.map((ev, ei) => (
                    <span key={ei} className="flex items-center gap-2">
                      <span className="font-mono text-xs sm:text-sm bg-neutral-100 px-2 py-0.5 rounded text-neutral-800">
                        {ev}
                      </span>
                      {ei < p.sequence.length - 1 && (
                        <span className="text-neutral-300">→</span>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            <div className="mt-3 sm:mt-0 flex items-baseline gap-6 text-sm shrink-0 ml-9 sm:ml-0">
              <div className="text-neutral-500 tabular-nums">
                <span className="text-neutral-900 font-medium">
                  {p.n_users.toLocaleString()}
                </span>{" "}
                users
              </div>
              <div className="text-neutral-500 tabular-nums">
                <span className="text-neutral-900 font-medium">
                  {(p.retain_pct * 100).toFixed(1)}%
                </span>{" "}
                retain
              </div>
              <div className="tabular-nums">
                <span className="font-semibold text-indigo-600">
                  {p.lift.toFixed(2)}×
                </span>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SpecsSection({ specs }: { specs: Spec[] }) {
  return (
    <section className="mt-20">
      <SectionHeader
        eyebrow="03"
        title="Generated experiment specs"
        desc="Top milestones sent to Claude Sonnet 4.6 via tool use. Each spec converts an observed correlation into a falsifiable hypothesis with a realistic expected effect."
      />
      <div className="space-y-3">
        {specs.map((s, i) => (
          <details
            key={i}
            className="group rounded-lg border border-neutral-200 bg-white overflow-hidden"
          >
            <summary className="cursor-pointer list-none px-5 py-4 hover:bg-neutral-50/60 transition-colors flex items-start justify-between gap-4">
              <div className="flex items-baseline gap-3 min-w-0">
                <span className="text-xs tabular-nums text-neutral-400 font-medium w-6 shrink-0">
                  {(i + 1).toString().padStart(2, "0")}
                </span>
                <div className="min-w-0">
                  <div className="font-medium text-neutral-900 truncate">
                    {prettyMilestone(s.milestone_name)}
                  </div>
                  <div className="mt-0.5 text-xs text-neutral-500">
                    Success event:{" "}
                    <code className="font-mono text-neutral-700">
                      {s.success_event}
                    </code>
                  </div>
                </div>
              </div>
              <span className="text-neutral-400 text-sm shrink-0 mt-0.5 group-open:rotate-90 transition-transform">
                ▸
              </span>
            </summary>
            <div className="border-t border-neutral-200 px-5 py-5 space-y-5">
              <SpecField label="Hypothesis">{s.hypothesis}</SpecField>
              <SpecField label="Target segment">{s.target_segment}</SpecField>
              <SpecField label="Success event">
                <code className="font-mono text-xs bg-neutral-100 px-1.5 py-0.5 rounded text-neutral-800">
                  {s.success_event}
                </code>
              </SpecField>
              <SpecField label="Guardrail metrics">
                <ul className="space-y-1.5 mt-1">
                  {s.guardrail_metrics.map((g, gi) => (
                    <li key={gi} className="flex gap-2">
                      <span className="text-neutral-300 shrink-0">—</span>
                      <span>{g}</span>
                    </li>
                  ))}
                </ul>
              </SpecField>
              <SpecField label="Expected effect size">
                <span className="text-indigo-700">{s.expected_effect_size}</span>
              </SpecField>
              <SpecField label="Rationale">
                <div className="whitespace-pre-line leading-relaxed">
                  {s.rationale}
                </div>
              </SpecField>
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

function SpecField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[180px_1fr] gap-x-6 gap-y-1">
      <div className="text-xs uppercase tracking-wider text-neutral-500 font-semibold sm:pt-0.5">
        {label}
      </div>
      <div className="text-sm text-neutral-800 leading-relaxed">{children}</div>
    </div>
  );
}

function Footer() {
  return (
    <footer className="mt-24 pt-8 border-t border-neutral-200 text-xs text-neutral-400">
      <p>
        Data: 25,000 synthetic users, 370,489 events, seed 42. Engine:
        Polars-based mining against Postgres. Synthesis: Claude Sonnet 4.6 with
        forced tool use.
      </p>
    </footer>
  );
}
