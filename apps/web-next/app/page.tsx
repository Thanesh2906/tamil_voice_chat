import { AuthGate } from "@/components/AuthGate";
import { WorkspaceProvider } from "@/components/WorkspaceContext";
import { OfficeDataProvider } from "@/components/OfficeDataContext";
import { RunDataProvider } from "@/components/RunDataContext";
import { OfficeDashboard } from "@/components/OfficeDashboard";

export default function Home() {
  return (
    <AuthGate>
      <WorkspaceProvider>
        <OfficeDataProvider>
          <RunDataProvider>
            <OfficeDashboard />
          </RunDataProvider>
        </OfficeDataProvider>
      </WorkspaceProvider>
    </AuthGate>
  );
}
