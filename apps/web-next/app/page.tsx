import { AuthGate } from "@/components/AuthGate";
import { WorkspaceProvider } from "@/components/WorkspaceContext";
import { OfficeDataProvider } from "@/components/OfficeDataContext";
import { OfficeDashboard } from "@/components/OfficeDashboard";

export default function Home() {
  return (
    <AuthGate>
      <WorkspaceProvider>
        <OfficeDataProvider>
          <OfficeDashboard />
        </OfficeDataProvider>
      </WorkspaceProvider>
    </AuthGate>
  );
}
