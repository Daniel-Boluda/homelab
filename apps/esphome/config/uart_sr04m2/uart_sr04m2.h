#include "esphome.h"

class UartTextSensor : public Component, public TextSensor {
 public:
  UartTextSensor(UARTComponent *uart) : uart_(uart) {}

  void loop() override {
    while (uart_->available()) {
      char c = uart_->read();
      if (c == '\n') {
        publish_state(buffer_.c_str());
        buffer_.clear();
      } else if (c != '\r') {
        buffer_ += c;
      }
    }
  }

 protected:
  UARTComponent *uart_;
  std::string buffer_;
};
