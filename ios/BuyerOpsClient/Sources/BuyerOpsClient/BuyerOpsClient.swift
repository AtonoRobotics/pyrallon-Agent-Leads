import CryptoKit
import Foundation
import Security

public struct JourneyView: Codable, Sendable, Equatable {
    public let journeyID: String
    public let tenantID: String
    public let canonicalVersion: Int
    public let etag: String
    public let states: [String: String]
    public let blockers: [String]
    public let sourceReferences: [String]

    enum CodingKeys: String, CodingKey {
        case journeyID = "journey_id"
        case tenantID = "tenant_id"
        case canonicalVersion = "canonical_version"
        case etag, states, blockers
        case sourceReferences = "source_references"
    }

    public init(journeyID: String, tenantID: String, canonicalVersion: Int, etag: String,
                states: [String: String], blockers: [String], sourceReferences: [String]) {
        self.journeyID = journeyID
        self.tenantID = tenantID
        self.canonicalVersion = canonicalVersion
        self.etag = etag
        self.states = states
        self.blockers = blockers
        self.sourceReferences = sourceReferences
    }
}

public struct WorkspaceSnapshot: Codable, Sendable, Equatable {
    public let journeys: [JourneyView]
    public let fetchedAt: Date

    public init(journeys: [JourneyView], fetchedAt: Date = Date()) {
        self.journeys = journeys
        self.fetchedAt = fetchedAt
    }
}

public enum BuyerOpsClientError: Error, Equatable {
    case authenticationRequired
    case reauthenticationRequired
    case tenantRequired
    case server(status: Int)
    case invalidResponse
    case offline
    case cacheUnavailable
}

public protocol AccessTokenProvider: Sendable {
    func validAccessToken() async throws -> String
    func reauthenticate() async throws -> String
}

public protocol OfflineSnapshotStore: Sendable {
    func read() throws -> WorkspaceSnapshot?
    func write(_ snapshot: WorkspaceSnapshot) throws
    func clear() throws
}

public struct PendingOperatorCommand: Codable, Sendable, Equatable {
    public let queueEntryID: String
    public let capturedAt: Date
    public let capturedCanonicalVersion: Int
    public let command: Data
    public let revalidateOnReconnect: Bool

    public init(queueEntryID: String = UUID().uuidString, capturedAt: Date = Date(),
                capturedCanonicalVersion: Int, command: Data) {
        self.queueEntryID = queueEntryID
        self.capturedAt = capturedAt
        self.capturedCanonicalVersion = capturedCanonicalVersion
        self.command = command
        self.revalidateOnReconnect = true
    }
}

public protocol OfflineCommandStore: Sendable {
    func readCommands() throws -> [PendingOperatorCommand]
    func writeCommands(_ commands: [PendingOperatorCommand]) throws
    func clearCommands() throws
}

public struct KeychainAccessTokenProvider: AccessTokenProvider {
    private let service: String
    private let account: String
    private let reauthenticateHandler: @Sendable () async throws -> String

    public init(service: String, account: String,
                reauthenticate: @escaping @Sendable () async throws -> String) {
        self.service = service
        self.account = account
        self.reauthenticateHandler = reauthenticate
    }

    public func validAccessToken() async throws -> String {
        guard let data = readKeychain(service: service, account: account),
              let token = String(data: data, encoding: .utf8), !token.isEmpty else {
            throw BuyerOpsClientError.reauthenticationRequired
        }
        return token
    }

    public func reauthenticate() async throws -> String {
        let token = try await reauthenticateHandler()
        guard !token.isEmpty else { throw BuyerOpsClientError.authenticationRequired }
        saveKeychain(Data(token.utf8), service: service, account: account)
        return token
    }
}

public final class EncryptedFileSnapshotStore: OfflineSnapshotStore, @unchecked Sendable {
    private let fileURL: URL
    private let keyTag: String
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    public init(directory: URL, keyTag: String = "buyer-ops.workspace-cache.v1") throws {
        self.fileURL = directory.appendingPathComponent("workspace.snapshot.enc")
        self.keyTag = keyTag
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    public func read() throws -> WorkspaceSnapshot? {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        let sealed = try AES.GCM.SealedBox(combined: Data(contentsOf: fileURL))
        return try decoder.decode(WorkspaceSnapshot.self, from: sealed.open(using: key()))
    }

    public func write(_ snapshot: WorkspaceSnapshot) throws {
        let encrypted = try AES.GCM.seal(encoder.encode(snapshot), using: key())
        guard let combined = encrypted.combined else { throw BuyerOpsClientError.cacheUnavailable }
        try combined.write(to: fileURL, options: [.atomic, .completeFileProtection])
    }

    public func clear() throws {
        if FileManager.default.fileExists(atPath: fileURL.path) {
            try FileManager.default.removeItem(at: fileURL)
        }
    }

    private func key() throws -> SymmetricKey {
        if let data = readKeychain(service: "BuyerOpsCacheKey", account: keyTag) {
            return SymmetricKey(data: data)
        }
        let data = Data((0..<32).map { _ in UInt8.random(in: 0...255) })
        saveKeychain(data, service: "BuyerOpsCacheKey", account: keyTag)
        return SymmetricKey(data: data)
    }
}

public final class EncryptedFileCommandStore: OfflineCommandStore, @unchecked Sendable {
    private let fileURL: URL
    private let keyTag: String

    public init(directory: URL, keyTag: String = "buyer-ops.command-queue.v1") throws {
        self.fileURL = directory.appendingPathComponent("operator.commands.enc")
        self.keyTag = keyTag
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    public func readCommands() throws -> [PendingOperatorCommand] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return [] }
        let sealed = try AES.GCM.SealedBox(combined: Data(contentsOf: fileURL))
        return try JSONDecoder.buyerOps.decode([PendingOperatorCommand].self, from: sealed.open(using: key()))
    }

    public func writeCommands(_ commands: [PendingOperatorCommand]) throws {
        let data = try JSONEncoder.buyerOps.encode(commands)
        let sealed = try AES.GCM.seal(data, using: key())
        guard let combined = sealed.combined else { throw BuyerOpsClientError.cacheUnavailable }
        try combined.write(to: fileURL, options: [.atomic, .completeFileProtection])
    }

    public func clearCommands() throws {
        if FileManager.default.fileExists(atPath: fileURL.path) { try FileManager.default.removeItem(at: fileURL) }
    }

    private func key() throws -> SymmetricKey {
        if let data = readKeychain(service: "BuyerOpsCacheKey", account: keyTag) { return SymmetricKey(data: data) }
        let data = Data((0..<32).map { _ in UInt8.random(in: 0...255) })
        saveKeychain(data, service: "BuyerOpsCacheKey", account: keyTag)
        return SymmetricKey(data: data)
    }
}

public actor BuyerOpsClient {
    private let baseURL: URL
    private let tenantID: String
    private let actorID: String
    private let tokenProvider: AccessTokenProvider
    private let cache: OfflineSnapshotStore
    private let session: URLSession
    private let commandStore: OfflineCommandStore?
    private var pendingRefresh = false

    public init(baseURL: URL, tenantID: String, actorID: String, tokenProvider: AccessTokenProvider,
                cache: OfflineSnapshotStore, commandStore: OfflineCommandStore? = nil,
                session: URLSession = .shared) throws {
        guard !tenantID.isEmpty else { throw BuyerOpsClientError.tenantRequired }
        guard !actorID.isEmpty else { throw BuyerOpsClientError.authenticationRequired }
        self.baseURL = baseURL
        self.tenantID = tenantID
        self.actorID = actorID
        self.tokenProvider = tokenProvider
        self.cache = cache
        self.commandStore = commandStore
        self.session = session
    }

    public func workspace() async throws -> WorkspaceSnapshot {
        do {
            let token = try await tokenProvider.validAccessToken()
            let request = try makeRequest(path: "/v1/workspace", token: token)
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw BuyerOpsClientError.invalidResponse }
            if http.statusCode == 401 && !pendingRefresh {
                pendingRefresh = true
                defer { pendingRefresh = false }
                _ = try await tokenProvider.reauthenticate()
                return try await workspace()
            }
            guard (200..<300).contains(http.statusCode) else { throw BuyerOpsClientError.server(status: http.statusCode) }
            let snapshot = try JSONDecoder.buyerOps.decode(WorkspaceSnapshot.self, from: data)
            try cache.write(snapshot)
            return snapshot
        } catch let error as BuyerOpsClientError {
            if case .server = error { throw error }
            return try cachedOrRethrow(error)
        } catch {
            return try cachedOrRethrow(.offline)
        }
    }

    public func clearSession() throws {
        try cache.clear()
        try commandStore?.clearCommands()
    }

    public func queue(command: Data, capturedCanonicalVersion: Int) throws -> PendingOperatorCommand {
        guard let commandStore else { throw BuyerOpsClientError.cacheUnavailable }
        let entry = PendingOperatorCommand(capturedCanonicalVersion: capturedCanonicalVersion, command: command)
        var pending = try commandStore.readCommands()
        pending.append(entry)
        try commandStore.writeCommands(pending)
        return entry
    }

    public func pendingCommands() throws -> [PendingOperatorCommand] {
        try commandStore?.readCommands() ?? []
    }

    public func reconnect() async throws -> [[String: String]] {
        guard let commandStore else { return [] }
        var pending = try commandStore.readCommands()
        guard !pending.isEmpty else { return [] }
        let token = try await authenticatedToken()
        var results: [[String: String]] = []
        var remaining: [PendingOperatorCommand] = []
        for entry in pending {
            do {
                let response = try await postCommand(entry.command, token: token)
                results.append(["queueEntryId": entry.queueEntryID, "status": response.status])
            } catch BuyerOpsClientError.server(let status) where status == 409 || status == 412 {
                results.append(["queueEntryId": entry.queueEntryID, "status": "offline_revalidation_failed"])
            } catch {
                remaining.append(entry)
                throw error
            }
        }
        pending = remaining
        try commandStore.writeCommands(pending)
        return results
    }

    private func makeRequest(path: String, token: String) throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw BuyerOpsClientError.invalidResponse }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue(tenantID, forHTTPHeaderField: "X-Buyer-Ops-Tenant")
        request.setValue(actorID, forHTTPHeaderField: "X-Buyer-Ops-Actor")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    private func authenticatedToken() async throws -> String {
        do { return try await tokenProvider.validAccessToken() }
        catch BuyerOpsClientError.reauthenticationRequired { return try await tokenProvider.reauthenticate() }
    }

    private func postCommand(_ data: Data, token: String) async throws -> (status: String) {
        guard let url = URL(string: "/v1/commands", relativeTo: baseURL) else { throw BuyerOpsClientError.invalidResponse }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = data
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue(tenantID, forHTTPHeaderField: "X-Buyer-Ops-Tenant")
        request.setValue(actorID, forHTTPHeaderField: "X-Buyer-Ops-Actor")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (body, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw BuyerOpsClientError.invalidResponse }
        if http.statusCode == 401 { _ = try await tokenProvider.reauthenticate(); throw BuyerOpsClientError.reauthenticationRequired }
        guard (200..<300).contains(http.statusCode) else { throw BuyerOpsClientError.server(status: http.statusCode) }
        guard let object = try JSONSerialization.jsonObject(with: body) as? [String: Any], let status = object["status"] as? String else { throw BuyerOpsClientError.invalidResponse }
        return (status)
    }

    private func cachedOrRethrow(_ error: Error) throws -> WorkspaceSnapshot {
        if let cached = try cache.read() { return cached }
        if let typed = error as? BuyerOpsClientError { throw typed }
        throw BuyerOpsClientError.offline
    }
}

private extension JSONDecoder {
    static let buyerOps: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()
}

private extension JSONEncoder {
    static let buyerOps: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()
}

private func readKeychain(service: String, account: String) -> Data? {
    let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                                  kSecAttrAccount: account, kSecReturnData: true, kSecMatchLimit: kSecMatchLimitOne]
    var item: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess else { return nil }
    return item as? Data
}

private func saveKeychain(_ data: Data, service: String, account: String) {
    let query: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account]
    SecItemDelete(query as CFDictionary)
    var item = query
    item[kSecValueData] = data
    SecItemAdd(item as CFDictionary, nil)
}
