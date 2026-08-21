import XCTest
@testable import BuyerOpsClient

final class BuyerOpsClientTests: XCTestCase {
    func testJourneyViewRoundTripsTheAuthenticatedWorkspaceContract() throws {
        let journey = JourneyView(journeyID: "journey-1", tenantID: "tenant-1", canonicalVersion: 4,
                                  etag: "etag-1", states: ["journey": "consultation_ready"],
                                  blockers: [], sourceReferences: ["evidence-1"])
        let snapshot = WorkspaceSnapshot(journeys: [journey])
        let data = try JSONEncoder().encode(snapshot)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let decoded = try decoder.decode(WorkspaceSnapshot.self, from: data)
        XCTAssertEqual(decoded, snapshot)
    }

    func testPendingOperatorCommandRequiresReconnectRevalidation() throws {
        let command = PendingOperatorCommand(capturedCanonicalVersion: 7, command: Data("{}".utf8))
        XCTAssertTrue(command.revalidateOnReconnect)
        let data = try JSONEncoder().encode(command)
        let decoded = try JSONDecoder().decode(PendingOperatorCommand.self, from: data)
        XCTAssertEqual(decoded, command)
    }
}
