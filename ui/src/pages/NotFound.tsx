import { Compass } from "lucide-react";
import { Link } from "react-router-dom";

import { EmptyState } from "../components/ui";

export default function NotFound() {
  return (
    <EmptyState icon={Compass} title="Page not found" action={<Link to="/queue" className="text-body-md font-medium text-primary hover:underline">Go to the queue</Link>}>
      This address doesn't match any view.
    </EmptyState>
  );
}
