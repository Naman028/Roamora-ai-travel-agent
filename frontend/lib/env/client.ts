import { clientEnvSchema } from "@/lib/env/schemas";

export const clientEnv = clientEnvSchema.parse({
  NEXT_PUBLIC_APP_ENV: process.env.NEXT_PUBLIC_APP_ENV,
});
