import { toast } from "sonner";

// Port of admin/ui/core.js `toast(msg, bad)` onto sonner. Bad → error styling, else success.
export const notify = (msg: string, bad?: boolean) => (bad ? toast.error(msg) : toast.success(msg));
