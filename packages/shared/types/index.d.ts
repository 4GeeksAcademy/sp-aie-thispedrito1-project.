/**
 * Shared types for transversal project apps.
 * Extend with domain types (e.g. Location, Sale, Customer) as needed.
 */
export type Id = string;
export interface BaseEntity {
    id: Id;
    createdAt?: string;
    updatedAt?: string;
}
//# sourceMappingURL=index.d.ts.map