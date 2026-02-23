//
//  TimeSenseMacCompanionUITestsLaunchTests.swift
//  TimeSenseMacCompanionUITests
//
//  Created by Zoey Zhou on 1/29/26.
//
/Users/zoeyzhou/Desktop/AI Architect/mac/TimeSenseMacCompanionXcode/TimeSenseMacCompanion/TimeSenseMacCompanion/Assets.xcassets
import XCTest

final class TimeSenseMacCompanionUITestsLaunchTests: XCTestCase {

    override class var runsForEachTargetApplicationUIConfiguration: Bool {
        true
    }

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testLaunch() throws {
        let app = XCUIApplication()
        app.launch()

        // Insert steps here to perform after app launch but before taking a screenshot,
        // such as logging into a test account or navigating somewhere in the app

        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Launch Screen"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
