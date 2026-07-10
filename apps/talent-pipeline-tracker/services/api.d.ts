import { Candidate, CandidateNote, CandidateFormData } from '../types/tracker';
export declare const talentApi: {
    getRecords: () => Promise<Candidate[]>;
    getRecordById: (id: string) => Promise<Candidate>;
    createRecord: (data: CandidateFormData) => Promise<Candidate>;
    updateRecord: (id: string, data: CandidateFormData) => Promise<Candidate>;
    patchRecordStatus: (id: string, cleanPayload: Partial<Pick<Candidate, "status" | "stage">>) => Promise<Candidate>;
    getNotes: (candidateId: string) => Promise<CandidateNote[]>;
    addNote: (candidateId: string, content: string) => Promise<CandidateNote>;
    deleteNote: (candidateId: string, noteId: string | number) => Promise<void>;
};
//# sourceMappingURL=api.d.ts.map