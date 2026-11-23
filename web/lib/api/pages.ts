/** One page of a cursor-paginated list, as every list endpoint returns it. */
export type CursorPage<T> = { items: T[]; next_cursor: string | null };

/**
 * Every item of a cursor-paginated list: loads the first page, then follows `next_cursor` to
 * the end. For lists a page needs whole, such as everyone's roles; a failed page rejects.
 */
export async function allPages<T>(
  load: (cursor?: string) => Promise<CursorPage<T>>,
): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | undefined;
  do {
    const page = await load(cursor);
    items.push(...page.items);
    cursor = page.next_cursor ?? undefined;
  } while (cursor !== undefined);
  return items;
}
