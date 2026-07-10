export const filterClaims = (claims, filters) => {
    return claims.filter(claim => {
        return Object.entries(filters).every(([key, value]) => {
            return value === undefined || claim[key] === value;
        });
    });
};
export const filterAppointmentsByStatus = (appointments, status) => {
    return appointments.filter(app => status.includes(app.status));
};
export const sortClaimsById = (claims, direction) => {
    return [...claims].sort((a, b) => {
        return direction === "asc"
            ? a.claimId.localeCompare(b.claimId)
            : b.claimId.localeCompare(a.claimId);
    });
};
export const sortAppointmentsByDate = (appointments, direction) => {
    return [...appointments].sort((a, b) => {
        const dateA = new Date(a.scheduledDate).getTime();
        const dateB = new Date(b.scheduledDate).getTime();
        return direction === "asc" ? dateA - dateB : dateB - dateA;
    });
};
export const groupClaimsBy = (claims, key) => {
    return claims.reduce((acc, claim) => {
        const groupKey = claim[key];
        if (!acc[groupKey])
            acc[groupKey] = [];
        acc[groupKey].push(claim);
        return acc;
    }, {});
};
//# sourceMappingURL=collections.js.map