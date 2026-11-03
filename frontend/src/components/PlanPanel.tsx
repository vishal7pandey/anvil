interface PlanPanelProps {
  plan: { todos: Array<{ content: string; status: string }> };
}

export default function PlanPanel({ plan }: PlanPanelProps) {
  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Plan</h3>
      <div className="space-y-2">
        {plan.todos.map((todo, idx) => (
          <div
            key={idx}
            className="flex items-center gap-2"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                todo.status === 'completed'
                  ? 'bg-green-500'
                  : todo.status === 'in_progress'
                    ? 'bg-blue-500 animate-pulse'
                    : 'bg-slate-300'
              }`}
            />
            <span
              className={`text-sm ${
                todo.status === 'completed' ? 'text-slate-500 line-through' : 'text-slate-800'
              }`}
            >
              {todo.content}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}