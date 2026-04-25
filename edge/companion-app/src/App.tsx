import BrandHeader from "./components/BrandHeader";
import TabBar from "./components/TabBar";
import ConsoleTab from "./tabs/Console/ConsoleTab";
import SessionsTab from "./tabs/Sessions/SessionsTab";
import DashboardTab from "./tabs/Dashboard/DashboardTab";
import { useUIStore } from "./store/ui";

export default function App() {
  const activeTab = useUIStore((s) => s.activeTab);

  return (
    <div className="app-shell">
      <BrandHeader />
      <TabBar />
      <main className="app-main">
        {activeTab === "console" && <ConsoleTab />}
        {activeTab === "sessions" && <SessionsTab />}
        {activeTab === "dashboard" && <DashboardTab />}
      </main>
    </div>
  );
}
