interface NavbarProps {
  page: "dashboard" | "backtest";
  setPage: (p: "dashboard" | "backtest") => void;
}

export function Navbar({ page, setPage }: NavbarProps) {
  const tab = (id: "dashboard" | "backtest", label: string) => (
    <button
      onClick={() => setPage(id)}
      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
        page === id
          ? "bg-sky-500/15 text-sky-400"
          : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/30"
      }`}
    >
      {label}
    </button>
  );

  return (
    <nav className="bg-[#0e1a24]/90 border-b border-slate-700/20 px-4 md:px-6 py-2">
      <div className="max-w-7xl mx-auto flex items-center gap-4">
        <div className="flex items-center gap-2 mr-2">
          <img src="/logo.svg" alt="Sigma Kapital" className="h-7" />
        </div>
        <div className="flex gap-1">
          {tab("dashboard", "Dashboard")}
          {tab("backtest", "Backtest")}
        </div>
      </div>
    </nav>
  );
}
