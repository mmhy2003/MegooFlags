import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-6">
      <h1 className="text-3xl font-semibold">MegooFlags</h1>
      <p className="text-muted-foreground">
        Open source feature flags. Nothing to see yet.
      </p>
      <Button
        variant="outline"
        nativeButton={false}
        render={<a href="https://github.com/mmhy2003/MegooFlags" />}
      >
        GitHub
      </Button>
    </main>
  );
}
